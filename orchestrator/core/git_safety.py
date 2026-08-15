"""Git checks and recoverable pre-Codex workspace snapshots."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from orchestrator.core.cli_runner import CliResult, CliRunner
from orchestrator.exceptions import GitSafetyError
from orchestrator.memory import utc_now


@dataclass(frozen=True, slots=True)
class GitBackup:
    repository_root: Path
    head: str | None
    status: str
    backup_dir: Path


class GitSafety:
    """Validate Git and preserve dirty workspace state without altering it."""

    def __init__(self, runner: CliRunner, timeout_seconds: int = 30) -> None:
        self.runner = runner
        self.timeout_seconds = timeout_seconds

    def create_backup(self, workspace_dir: Path, phase_dir: Path, step_id: str) -> GitBackup:
        log_path = phase_dir / "logs" / "git-safety.log"
        inside = self._git(
            workspace_dir, log_path, "rev-parse", "--is-inside-work-tree", check=False
        )
        if inside.return_code != 0 or inside.stdout.strip() != "true":
            raise GitSafetyError(
                f"Codex safety check failed: {workspace_dir} is not inside a Git repository."
            )

        root_result = self._git(workspace_dir, log_path, "rev-parse", "--show-toplevel")
        repository_root = Path(root_result.stdout.strip()).resolve()
        status = self.status(workspace_dir, log_path)
        head_result = self._git(workspace_dir, log_path, "rev-parse", "HEAD", check=False)
        head = head_result.stdout.strip() if head_result.return_code == 0 else None

        backup_dir = phase_dir / "backups" / step_id
        backup_dir.mkdir(parents=True, exist_ok=False)
        if head:
            patch = self._git(workspace_dir, log_path, "diff", "--binary", "HEAD", "--", ".").stdout
        else:
            unstaged = self._git(workspace_dir, log_path, "diff", "--binary", "--", ".").stdout
            staged = self._git(
                workspace_dir, log_path, "diff", "--binary", "--cached", "--", "."
            ).stdout
            patch = staged + unstaged
        (backup_dir / "tracked-changes.patch").write_text(patch, encoding="utf-8")

        untracked_result = self._git(
            workspace_dir,
            log_path,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            ".",
        )
        untracked = [item for item in untracked_result.stdout.split("\0") if item]
        archived: list[str] = []
        with zipfile.ZipFile(backup_dir / "untracked-files.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            workspace_root = workspace_dir.resolve()
            for relative_name in untracked:
                source = (workspace_dir / relative_name).resolve()
                try:
                    source.relative_to(workspace_root)
                except ValueError as exc:
                    raise GitSafetyError(f"Unsafe untracked path reported by Git: {relative_name}") from exc
                if source.is_file() and not source.is_symlink():
                    archive.write(source, arcname=relative_name)
                    archived.append(relative_name)

        metadata = {
            "created_at": utc_now(),
            "repository_root": str(repository_root),
            "workspace": str(workspace_dir.resolve()),
            "head": head,
            "status": status,
            "untracked_files": archived,
        }
        (backup_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        return GitBackup(repository_root, head, status, backup_dir)

    def status(self, workspace_dir: Path, log_path: Path) -> str:
        return self._git(
            workspace_dir, log_path, "status", "--short", "--branch", "--", "."
        ).stdout.rstrip()

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
