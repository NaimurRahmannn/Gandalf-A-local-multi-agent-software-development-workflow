"""Safe, observable execution of external command-line tools."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from orchestrator.exceptions import CliExecutionError, CliNotFoundError, CliTimeoutError


@dataclass(frozen=True, slots=True)
class CliResult:
    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    log_path: Path


class CliRunner:
    """Run argument arrays without a shell and persist an execution transcript."""

    @staticmethod
    def split_command(command: str) -> tuple[str, ...]:
        if not command.strip():
            raise ValueError("CLI command cannot be empty.")
        parts = shlex.split(command, posix=os.name != "nt")
        # Windows shlex preserves wrapping quotes; CreateProcess expects them removed.
        cleaned = tuple(
            part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"} else part
            for part in parts
        )
        if not cleaned or any(not part for part in cleaned):
            raise ValueError("CLI command contains an empty argument.")
        return cleaned

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_path: Path,
        timeout_seconds: int,
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        redact_values: Sequence[str] = (),
    ) -> CliResult:
        requested_argv = tuple(str(part) for part in command)
        if not requested_argv or any(not part for part in requested_argv):
            raise ValueError("Command must contain only non-empty arguments.")
        argv = self._resolve_command(requested_argv)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if not cwd.is_dir():
            raise CliExecutionError(f"Command working directory does not exist: {cwd}")

        log_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        popen_options: dict[str, object] = {
            "cwd": cwd,
            "stdin": subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": process_env,
        }
        if os.name == "nt":
            popen_options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            popen_options["start_new_session"] = True

        try:
            process = subprocess.Popen(argv, **popen_options)  # type: ignore[arg-type]
        except FileNotFoundError as exc:
            self._write_log(log_path, started_at, argv, "", str(exc), 0.0, None, redact_values)
            raise CliNotFoundError(
                f"CLI executable '{requested_argv[0]}' was not found. "
                "Check installation and agents.*.command."
            ) from exc
        except OSError as exc:
            self._write_log(log_path, started_at, argv, "", str(exc), 0.0, None, redact_values)
            raise CliExecutionError(f"Unable to start '{argv[0]}': {exc}") from exc

        try:
            stdout, stderr = process.communicate(input=stdin, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._terminate_tree(process)
            stdout, stderr = process.communicate()
            duration = time.monotonic() - start
            self._write_log(
                log_path, started_at, argv, stdout, stderr, duration, None, redact_values,
                note=f"Timed out after {timeout_seconds} seconds",
            )
            raise CliTimeoutError(
                f"'{argv[0]}' timed out after {timeout_seconds} seconds. See {log_path}"
            ) from exc

        duration = time.monotonic() - start
        result = CliResult(argv, process.returncode, stdout, stderr, duration, log_path)
        self._write_log(
            log_path, started_at, argv, stdout, stderr, duration, process.returncode, redact_values
        )
        if check and process.returncode != 0:
            detail = stderr.strip() or stdout.strip() or "No diagnostic output."
            raise CliExecutionError(
                f"'{argv[0]}' exited with code {process.returncode}: {detail[:500]} See {log_path}"
            )
        return result

    @staticmethod
    def _resolve_command(command: tuple[str, ...]) -> tuple[str, ...]:
        """Resolve Windows PowerShell shims without evaluating a command string."""

        if os.name != "nt":
            return command
        executable = command[0]
        path = Path(executable)
        resolved: str | None = None
        if path.suffix:
            resolved = shutil.which(executable) or (str(path.resolve()) if path.is_file() else None)
        else:
            # npm and Cursor install PowerShell shims that CreateProcess cannot launch
            # directly. Prefer those over cmd/bat shims so prompt text stays argv data.
            resolved = (
                shutil.which(f"{executable}.ps1")
                or shutil.which(f"{executable}.exe")
                or shutil.which(f"{executable}.com")
                or shutil.which(executable)
            )
        if resolved is None:
            return command  # Popen produces the standard not-found diagnostic.
        suffix = Path(resolved).suffix.lower()
        if suffix == ".ps1":
            powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
            if powershell is None:
                raise CliNotFoundError(
                    f"PowerShell is required to launch configured CLI shim: {resolved}"
                )
            return (
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                resolved,
                *command[1:],
            )
        if suffix in {".cmd", ".bat"}:
            sibling = Path(resolved).with_suffix(".ps1")
            if sibling.is_file():
                powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
                if powershell is not None:
                    return (
                        powershell,
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(sibling),
                        *command[1:],
                    )
            raise CliExecutionError(
                f"Refusing to pass agent prompts through command-shell wrapper {resolved}. "
                "Configure a native executable or PowerShell shim instead."
            )
        return (resolved, *command[1:])

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # Restricted Windows environments can deny taskkill's tree traversal.
            # Killing the direct process still guarantees communicate() can return.
            if process.poll() is None:
                process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)

    @staticmethod
    def _write_log(
        path: Path,
        started_at: str,
        command: Sequence[str],
        stdout: str,
        stderr: str,
        duration: float,
        return_code: int | None,
        redact_values: Sequence[str],
        note: str | None = None,
    ) -> None:
        displayed = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
        for value in redact_values:
            if value:
                displayed = displayed.replace(value, "<redacted-prompt>")
        content = (
            f"Started (UTC): {started_at}\n"
            f"Command: {displayed}\n"
            f"Duration: {duration:.3f}s\n"
            f"Exit code: {return_code if return_code is not None else 'not available'}\n"
        )
        if note:
            content += f"Note: {note}\n"
        content += f"\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(content)
