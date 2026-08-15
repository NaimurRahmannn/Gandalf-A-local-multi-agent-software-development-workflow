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
from orchestrator.config import AgentConfig, AppConfig, PathsConfig
from orchestrator.core.cli_runner import CliRunner
from orchestrator.exceptions import AgentExecutionError
from orchestrator.memory import MEMORY_FILES, MemoryStore
from orchestrator.models import AgentContext, AgentResult
from orchestrator.workflow import WorkflowManager


class FailingAgent(BaseAgent):
    name = "cursor"
    role = "test failure"
    instructions = "fail"

    def execute(self, context: AgentContext) -> AgentResult:
        raise RuntimeError("simulated integration failure")


class StaticAgent(BaseAgent):
    def __init__(self, name: str) -> None:
        self.name = name
        self.role = "test"
        self.instructions = "test"

    def execute(self, context: AgentContext) -> AgentResult:
        return AgentResult(self.name, context.step_id, f"{self.name} result", "complete")


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "runtime" / uuid4().hex
        self.root.mkdir(parents=True)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        subprocess.run(
            ("git", "init", "--quiet"), cwd=self.workspace, check=True, capture_output=True
        )
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

    def test_real_adapters_complete_five_stage_workflow(self) -> None:
        manager = WorkflowManager(
            self.config, build_agents(self.config, CliRunner()), self.store
        )
        phase_dir = manager.run("Build authentication system")
        state = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))

        self.assertEqual("completed", state["status"])
        self.assertEqual(5, len(list((phase_dir / "tasks").glob("*.md"))))
        for artifact in (
            "antigravity-plan.md",
            "codex-report.md",
            "cursor-review.md",
            "antigravity-final-review.md",
            "codex-improvement-report.md",
        ):
            self.assertTrue((phase_dir / artifact).is_file(), artifact)
        self.assertTrue((phase_dir / "backups" / "implementation" / "metadata.json").is_file())
        self.assertTrue((phase_dir / "backups" / "improvement" / "untracked-files.zip").is_file())
        planning_prompt = (phase_dir / "logs" / "planning.prompt.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("Build authentication system", planning_prompt)
        self.assertIn("CTO, planner, and final decision maker", planning_prompt)
        self.assertIn("# Project", planning_prompt)
        self.assertIn("# Decisions", planning_prompt)
        self.assertEqual("preserve me\n", (self.workspace / "existing.txt").read_text(encoding="utf-8"))
        for filename in MEMORY_FILES:
            self.assertTrue((self.config.paths.memory_dir / filename).is_file())

    def test_agent_failure_is_persisted(self) -> None:
        agents = [StaticAgent("antigravity"), StaticAgent("codex"), FailingAgent()]
        manager = WorkflowManager(self.config, agents, self.store)
        with self.assertRaises(AgentExecutionError):
            manager.run("Trigger a failure")

        phase_dir = next(self.config.paths.phases_dir.iterdir())
        state = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", state["status"])
        review = next(step for step in state["steps"] if step["id"] == "review")
        self.assertEqual("failed", review["status"])
        self.assertIn("simulated integration failure", review["error"])


if __name__ == "__main__":
    unittest.main()
