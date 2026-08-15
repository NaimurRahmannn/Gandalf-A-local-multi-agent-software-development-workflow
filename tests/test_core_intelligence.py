from __future__ import annotations

import shutil
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from orchestrator.core.cli_runner import CliRunner
from orchestrator.core.git_manager import GitManager
from orchestrator.core.test_runner import TestRunner
from orchestrator.exceptions import GitCommitApprovalError


class CoreIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "runtime" / uuid4().hex
        self.root.mkdir(parents=True)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        subprocess.run(("git", "init", "--quiet"), cwd=self.workspace, check=True)
        subprocess.run(
            ("git", "config", "user.email", "tests@example.invalid"),
            cwd=self.workspace,
            check=True,
        )
        subprocess.run(
            ("git", "config", "user.name", "Orchestrator Tests"),
            cwd=self.workspace,
            check=True,
        )
        (self.workspace / "tracked.txt").write_text("before\n", encoding="utf-8")
        subprocess.run(("git", "add", "tracked.txt"), cwd=self.workspace, check=True)
        subprocess.run(("git", "commit", "--quiet", "-m", "initial"), cwd=self.workspace, check=True)
        self.runner = CliRunner()
        self.log_path = self.root / "git.log"

    def tearDown(self) -> None:
        def remove_readonly(function, path, _error):
            os.chmod(path, stat.S_IWRITE)
            function(path)

        shutil.rmtree(self.root, onexc=remove_readonly)

    def test_git_status_diff_files_checkpoint_and_commit_gate(self) -> None:
        (self.workspace / "tracked.txt").write_text("after\n", encoding="utf-8")
        (self.workspace / "untracked.txt").write_text("new\n", encoding="utf-8")
        manager = GitManager(self.runner)

        self.assertEqual(self.workspace.resolve(), manager.check_repository(self.workspace, self.log_path))
        self.assertIn("tracked.txt", manager.get_status(self.workspace, self.log_path))
        self.assertIn("-before", manager.get_diff(self.workspace, self.log_path))
        self.assertEqual(("tracked.txt", "untracked.txt"), manager.get_changed_files(self.workspace, self.log_path))
        checkpoint = manager.create_checkpoint(
            self.workspace,
            self.log_path,
            self.root / "checkpoint.txt",
            label="before tests",
        )
        self.assertIsNotNone(checkpoint.head)
        with self.assertRaises(GitCommitApprovalError):
            manager.create_commit(self.workspace, self.log_path, "blocked")

        approved_manager = GitManager(self.runner, allow_commit=True)
        commit = approved_manager.create_commit(self.workspace, self.log_path, "approved checkpoint")
        self.assertTrue(commit)
        self.assertEqual((), approved_manager.get_changed_files(self.workspace, self.log_path))

    def test_test_runner_records_pass_and_failure(self) -> None:
        commands = (
            f'"{sys.executable}" -c "print(123)"',
            f'"{sys.executable}" -c "import sys; sys.exit(4)"',
        )
        phase_dir = self.root / "phase"
        (phase_dir / "logs").mkdir(parents=True)
        suite = TestRunner(self.runner, commands, 10).run(self.workspace, phase_dir, 1)

        self.assertFalse(suite.passed)
        self.assertEqual((True, False), tuple(item.passed for item in suite.results))
        report = (phase_dir / "test-results.md").read_text(encoding="utf-8")
        self.assertIn("Overall: FAIL", report)
        self.assertIn("Exit code: 4", report)
