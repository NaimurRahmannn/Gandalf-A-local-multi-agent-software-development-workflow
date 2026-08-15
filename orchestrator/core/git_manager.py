"""Git intelligence for phase snapshots, review context, and gated commits."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.core.cli_runner import CliResult, CliRunner
from orchestrator.exceptions import GitCommitApprovalError, GitSafetyError
from orchestrator.memory import utc_now


@dataclass(frozen=True, slots=True)
class GitCheckpoint:
    repository_root: Path
    head: str | None
    status: str
    label: str


class GitManager:
    """Read repository state and perform only explicitly approved commits."""

    def __init__(
        self,
        runner: CliRunner,
        *,
        allow_commit: bool = False,
        timeout_seconds: int = 60,
    ) -> None:
        self.runner = runner
        self.allow_commit = allow_commit
        self.timeout_seconds = timeout_seconds

    def check_repository(self, workspace_dir: Path, log_path: Path) -> Path:
        result = self._git(
            workspace_dir, log_path, "rev-parse", "--is-inside-work-tree", check=False
        )
        if result.return_code != 0 or result.stdout.strip() != "true":
            raise GitSafetyError(f"Workspace is not inside a Git repository: {workspace_dir}")
        root = self._git(workspace_dir, log_path, "rev-parse", "--show-toplevel").stdout.strip()
        return Path(root).resolve()

    def get_status(self, workspace_dir: Path, log_path: Path) -> str:
        self.check_repository(workspace_dir, log_path)
        return self._git(
            workspace_dir, log_path, "status", "--short", "--branch", "--", "."
        ).stdout.rstrip()

    def get_diff(self, workspace_dir: Path, log_path: Path) -> str:
        self.check_repository(workspace_dir, log_path)
        head = self._git(workspace_dir, log_path, "rev-parse", "HEAD", check=False)
        if head.return_code == 0:
            return self._git(
                workspace_dir, log_path, "diff", "--binary", "HEAD", "--", "."
            ).stdout
        staged = self._git(
            workspace_dir, log_path, "diff", "--binary", "--cached", "--", "."
        ).stdout
        unstaged = self._git(
            workspace_dir, log_path, "diff", "--binary", "--", "."
        ).stdout
        return staged + unstaged

    def get_changed_files(self, workspace_dir: Path, log_path: Path) -> tuple[str, ...]:
        self.check_repository(workspace_dir, log_path)
        head = self._git(workspace_dir, log_path, "rev-parse", "HEAD", check=False)
        changed: set[str] = set()
        if head.return_code == 0:
            output = self._git(
                workspace_dir, log_path, "diff", "--name-only", "HEAD", "--", "."
            ).stdout
            changed.update(line for line in output.splitlines() if line)
        else:
            for arguments in (
                ("diff", "--name-only", "--cached", "--", "."),
                ("diff", "--name-only", "--", "."),
            ):
                output = self._git(workspace_dir, log_path, *arguments).stdout
                changed.update(line for line in output.splitlines() if line)
        untracked = self._git(
            workspace_dir,
            log_path,
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            ".",
        ).stdout
        changed.update(line for line in untracked.splitlines() if line)
        return tuple(sorted(changed))

    def create_checkpoint(
        self,
        workspace_dir: Path,
        log_path: Path,
        output_path: Path,
        *,
        label: str,
    ) -> GitCheckpoint:
        repository_root = self.check_repository(workspace_dir, log_path)
        head_result = self._git(workspace_dir, log_path, "rev-parse", "HEAD", check=False)
        head = head_result.stdout.strip() if head_result.return_code == 0 else None
        status = self.get_status(workspace_dir, log_path)
        checkpoint = GitCheckpoint(repository_root, head, status, label)
        output_path.write_text(
            f"Checkpoint: {label}\n"
            f"Created (UTC): {utc_now()}\n"
            f"Repository: {repository_root}\n"
            f"Workspace: {workspace_dir.resolve()}\n"
            f"HEAD: {head or 'unborn HEAD'}\n\n"
            f"Status:\n{status or 'Clean'}\n",
            encoding="utf-8",
        )
        return checkpoint

    def create_commit(self, workspace_dir: Path, log_path: Path, message: str) -> str:
        if not self.allow_commit:
            raise GitCommitApprovalError(
                "Git commit blocked: set git.allow_commit: true to approve automatic commits."
            )
        if not message.strip():
            raise GitSafetyError("Git commit message cannot be empty.")
        self.check_repository(workspace_dir, log_path)
        if not self.get_changed_files(workspace_dir, log_path):
            head = self._git(workspace_dir, log_path, "rev-parse", "HEAD", check=False)
            return head.stdout.strip() if head.return_code == 0 else ""
        self._git(workspace_dir, log_path, "add", "--", ".")
        self._git(workspace_dir, log_path, "commit", "--only", "-m", message, "--", ".")
        return self._git(workspace_dir, log_path, "rev-parse", "HEAD").stdout.strip()

    def _git(
        self, cwd: Path, log_path: Path, *arguments: str, check: bool = True
    ) -> CliResult:
        return self.runner.run(
            ("git", *arguments),
            cwd=cwd,
            log_path=log_path,
            timeout_seconds=self.timeout_seconds,
            check=check,
        )
