"""Configured project test execution with durable Markdown results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from orchestrator.core.cli_runner import CliRunner
from orchestrator.exceptions import CliRunnerError
from orchestrator.memory import utc_now


@dataclass(frozen=True, slots=True)
class TestCommandResult:
    command: str
    passed: bool
    return_code: int | None
    stdout: str
    stderr: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class TestSuiteResult:
    cycle: int
    results: tuple[TestCommandResult, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)


class TestRunner:
    def __init__(
        self,
        cli_runner: CliRunner,
        commands: Sequence[str],
        timeout_seconds: int,
    ) -> None:
        self.cli_runner = cli_runner
        self.commands = tuple(commands)
        self.timeout_seconds = timeout_seconds

    def run(self, workspace_dir: Path, phase_dir: Path, cycle: int) -> TestSuiteResult:
        results: list[TestCommandResult] = []
        for index, command_text in enumerate(self.commands, start=1):
            try:
                result = self.cli_runner.run(
                    self.cli_runner.split_command(command_text),
                    cwd=workspace_dir,
                    log_path=phase_dir / "logs" / f"tests-cycle-{cycle}-{index}.log",
                    timeout_seconds=self.timeout_seconds,
                    check=False,
                )
                results.append(
                    TestCommandResult(
                        command_text,
                        result.return_code == 0,
                        result.return_code,
                        result.stdout,
                        result.stderr,
                    )
                )
            except CliRunnerError as exc:
                results.append(TestCommandResult(command_text, False, None, "", "", str(exc)))
        suite = TestSuiteResult(cycle, tuple(results))
        self._append_results(phase_dir / "test-results.md", suite)
        return suite

    @staticmethod
    def to_markdown(suite: TestSuiteResult) -> str:
        if not suite.results:
            return f"## Cycle {suite.cycle}\n\nNo test commands configured.\n"
        sections = [
            f"## Cycle {suite.cycle}",
            f"Overall: {'PASS' if suite.passed else 'FAIL'}",
        ]
        for result in suite.results:
            sections.append(
                f"### `{result.command}`\n\n"
                f"- Result: {'PASS' if result.passed else 'FAIL'}\n"
                f"- Exit code: {result.return_code if result.return_code is not None else 'unavailable'}\n"
                f"- Error: {result.error or 'none'}\n\n"
                f"#### stdout\n\n```text\n{result.stdout.rstrip()}\n```\n\n"
                f"#### stderr\n\n```text\n{result.stderr.rstrip()}\n```"
            )
        return "\n\n".join(sections) + "\n"

    def _append_results(self, path: Path, suite: TestSuiteResult) -> None:
        heading = "# Test Results\n\n" if not path.exists() or path.stat().st_size == 0 else "\n"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{heading}Recorded (UTC): {utc_now()}\n\n{self.to_markdown(suite)}")
