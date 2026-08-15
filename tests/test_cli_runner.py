from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from orchestrator.core.cli_runner import CliRunner
from orchestrator.exceptions import CliExecutionError, CliNotFoundError, CliTimeoutError


class CliRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "runtime" / uuid4().hex
        self.root.mkdir(parents=True)
        self.runner = CliRunner()

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_captures_stdout_stderr_and_log(self) -> None:
        result = self.runner.run(
            (sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"),
            cwd=self.root,
            log_path=self.root / "command.log",
            timeout_seconds=5,
        )
        self.assertEqual(0, result.return_code)
        self.assertEqual("out", result.stdout.strip())
        self.assertEqual("err", result.stderr.strip())
        self.assertIn("--- stderr ---", result.log_path.read_text(encoding="utf-8"))

    def test_nonzero_exit_raises_and_is_logged(self) -> None:
        log_path = self.root / "failure.log"
        with self.assertRaises(CliExecutionError):
            self.runner.run(
                (sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(7)"),
                cwd=self.root,
                log_path=log_path,
                timeout_seconds=5,
            )
        self.assertIn("Exit code: 7", log_path.read_text(encoding="utf-8"))

    def test_timeout_terminates_command(self) -> None:
        with self.assertRaises(CliTimeoutError):
            self.runner.run(
                (sys.executable, "-c", "import time; time.sleep(10)"),
                cwd=self.root,
                log_path=self.root / "timeout.log",
                timeout_seconds=1,
            )

    def test_missing_executable_has_clear_error(self) -> None:
        with self.assertRaises(CliNotFoundError):
            self.runner.run(
                ("definitely-not-an-ai-cli",),
                cwd=self.root,
                log_path=self.root / "missing.log",
                timeout_seconds=5,
            )
