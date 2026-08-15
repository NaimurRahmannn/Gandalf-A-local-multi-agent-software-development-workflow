from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from orchestrator.agents import build_agents
from orchestrator.agents.base import BaseAgent
from orchestrator.config import AgentConfig, AppConfig, PathsConfig, WorkflowConfig
from orchestrator.core.cli_runner import CliRunner
from orchestrator.core.git_manager import GitManager
from orchestrator.core.test_runner import TestRunner
from orchestrator.exceptions import AgentExecutionError
from orchestrator.memory import MEMORY_FILES, PHASE_DOCUMENTS, MemoryStore
from orchestrator.models import AgentContext, AgentResult
from orchestrator.workflow import WorkflowManager


class TeamAgent(BaseAgent):
    def __init__(self, name: str) -> None:
        self.name = name
        self.role = "test"
        self.instructions = "test"

    def execute(self, context: AgentContext) -> AgentResult:
        if self.name == "codex":
            target = context.workspace_dir / "feature.txt"
            previous = target.read_text(encoding="utf-8") if target.exists() else ""
            target.write_text(previous + f"{context.step_id}\n", encoding="utf-8")
        content = "complete"
        if self.name == "antigravity" and context.step_id.startswith("architecture-review"):
            content += "\n\nREVIEW_DECISION: APPROVED"
        return AgentResult(self.name, context.step_id, f"{self.name} result", content)


class IteratingAntigravity(TeamAgent):
    def __init__(self) -> None:
        super().__init__("antigravity")
        self.review_count = 0

    def execute(self, context: AgentContext) -> AgentResult:
        if context.step_id.startswith("architecture-review"):
            self.review_count += 1
            decision = "CHANGES_REQUIRED" if self.review_count == 1 else "APPROVED"
            return AgentResult(self.name, context.step_id, "architecture decision", f"Fix issue.\n\nREVIEW_DECISION: {decision}")
        return super().execute(context)


class FailingOnceCursor(TeamAgent):
    def __init__(self) -> None:
        super().__init__("cursor")
        self.should_fail = True

    def execute(self, context: AgentContext) -> AgentResult:
        if self.should_fail:
            raise RuntimeError("simulated integration failure")
        return super().execute(context)


class InterruptingOnceCursor(FailingOnceCursor):
    def execute(self, context: AgentContext) -> AgentResult:
        if self.should_fail:
            raise KeyboardInterrupt()
        return TeamAgent.execute(self, context)


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "runtime" / uuid4().hex
        self.root.mkdir(parents=True)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        subprocess.run(("git", "init", "--quiet"), cwd=self.workspace, check=True)
        (self.workspace / "existing.txt").write_text("preserve me\n", encoding="utf-8")
        prompts = Path(__file__).resolve().parents[1] / "orchestrator" / "prompts"
        paths = PathsConfig(
            memory_dir=self.root / ".ai-memory",
            workspace_dir=self.workspace,
            phases_dir=self.root / ".ai-memory" / "phases",
            prompts_dir=prompts,
        )
        fake_cli = Path(__file__).resolve().parent / "fake_agent_cli.py"
        command = f'"{sys.executable}"'
        self.config = AppConfig(
            project_root=self.root,
            paths=paths,
            agents={
                "antigravity": AgentConfig(True, command, (str(fake_cli), "antigravity", "{prompt}"), 10),
                "codex": AgentConfig(True, command, (str(fake_cli), "codex"), 10),
                "cursor": AgentConfig(True, command, (str(fake_cli), "cursor", "{prompt}"), 10),
            },
        )
        self.store = MemoryStore(paths.memory_dir, paths.phases_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def manager(self, agents: list[BaseAgent]) -> WorkflowManager:
        runner = CliRunner()
        return WorkflowManager(
            self.config,
            agents,
            self.store,
            GitManager(runner),
            TestRunner(runner, (), 10),
        )

    def test_real_adapters_create_phase_three_snapshots(self) -> None:
        runner = CliRunner()
        manager = WorkflowManager(
            self.config,
            build_agents(self.config, runner),
            self.store,
            GitManager(runner),
            TestRunner(runner, (), 10),
        )
        phase_dir = manager.run("Build authentication system")
        state = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))

        self.assertEqual("completed", state["status"])
        self.assertTrue(state["approved"])
        for artifact in PHASE_DOCUMENTS:
            self.assertTrue((phase_dir / artifact).is_file(), artifact)
        for artifact in (
            "antigravity-plan.md",
            "codex-report.md",
            "cursor-review.md",
            "antigravity-final-review.md",
        ):
            self.assertTrue((phase_dir / artifact).is_file(), artifact)
        self.assertTrue((phase_dir / "backups" / "implementation" / "metadata.json").is_file())
        self.assertIn("existing.txt", (phase_dir / "before-state.txt").read_text(encoding="utf-8"))
        self.assertIn("# Phase Report", (phase_dir / "phase-report.md").read_text(encoding="utf-8"))
        self.assertIn("# Test Results", (phase_dir / "test-results.md").read_text(encoding="utf-8"))
        planning_prompt = (phase_dir / "logs" / "planning.prompt.txt").read_text(encoding="utf-8")
        self.assertIn("Build authentication system", planning_prompt)
        self.assertIn("# Decisions", planning_prompt)
        self.assertEqual("preserve me\n", (self.workspace / "existing.txt").read_text(encoding="utf-8"))
        for filename in MEMORY_FILES:
            self.assertTrue((self.config.paths.memory_dir / filename).is_file())
        self.assertIn(state["phase_id"], (self.config.paths.memory_dir / "decisions.md").read_text(encoding="utf-8"))
        self.assertIn(state["phase_id"], (self.config.paths.memory_dir / "architecture.md").read_text(encoding="utf-8"))
        self.assertIn(state["phase_id"], (self.config.paths.memory_dir / "progress.md").read_text(encoding="utf-8"))

    def test_changes_required_runs_improvement_and_second_review(self) -> None:
        self.config = AppConfig(
            self.config.project_root,
            self.config.paths,
            self.config.agents,
            workflow=WorkflowConfig(max_review_cycles=3, require_approval=True),
        )
        antigravity = IteratingAntigravity()
        phase_dir = self.manager(
            [antigravity, TeamAgent("codex"), TeamAgent("cursor")]
        ).run("Iterate until approved")
        state = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))

        self.assertEqual("completed", state["status"])
        self.assertEqual(2, state["review_cycle"])
        self.assertEqual(2, antigravity.review_count)
        self.assertIn("improvement-cycle-1", (phase_dir / "improvements.md").read_text(encoding="utf-8"))
        self.assertIn("review-cycle-2", (phase_dir / "review.md").read_text(encoding="utf-8"))
        self.assertIn("feature.txt", (phase_dir / "changes.diff").read_text(encoding="utf-8"))

    def test_failed_agent_can_resume_without_replaying_completed_steps(self) -> None:
        cursor = FailingOnceCursor()
        manager = self.manager([TeamAgent("antigravity"), TeamAgent("codex"), cursor])
        with self.assertRaises(AgentExecutionError):
            manager.run("Resume this phase")

        phase_dir = next(self.config.paths.phases_dir.iterdir())
        failed = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", failed["status"])
        self.assertEqual("review", failed["next_action"])
        planning_mtime = (phase_dir / "plan.md").stat().st_mtime_ns

        cursor.should_fail = False
        resumed_dir = manager.resume(failed["phase_id"])
        resumed = json.loads((resumed_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual("completed", resumed["status"])
        self.assertEqual(planning_mtime, (phase_dir / "plan.md").stat().st_mtime_ns)
        review = next(step for step in resumed["steps"] if step["id"] == "review-cycle-1")
        self.assertEqual("completed", review["status"])

    def test_interrupted_phase_can_resume(self) -> None:
        cursor = InterruptingOnceCursor()
        manager = self.manager([TeamAgent("antigravity"), TeamAgent("codex"), cursor])
        with self.assertRaises(KeyboardInterrupt):
            manager.run("Interrupt this phase")

        phase_dir = next(self.config.paths.phases_dir.iterdir())
        interrupted = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual("interrupted", interrupted["status"])
        cursor.should_fail = False
        manager.resume(interrupted["phase_id"])
        resumed = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual("completed", resumed["status"])

    def test_required_approval_marks_unapproved_phase_for_attention(self) -> None:
        self.config = AppConfig(
            self.config.project_root,
            self.config.paths,
            self.config.agents,
            workflow=WorkflowConfig(max_review_cycles=1, require_approval=True),
        )
        phase_dir = self.manager(
            [IteratingAntigravity(), TeamAgent("codex"), TeamAgent("cursor")]
        ).run("Do not approve this cycle")
        state = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))

        self.assertEqual("needs_attention", state["status"])
        self.assertFalse(state["approved"])
        self.assertIn("Resolve remaining review issues", (phase_dir / "phase-report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
