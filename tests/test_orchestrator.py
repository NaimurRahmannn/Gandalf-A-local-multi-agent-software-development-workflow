from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from orchestrator.agents import AntigravityAgent, CodexAgent, CursorAgent
from orchestrator.agents.base import BaseAgent
from orchestrator.config import AgentConfig, AppConfig, PathsConfig, load_config
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


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "runtime" / uuid4().hex
        self.root.mkdir(parents=True)
        paths = PathsConfig(
            memory_dir=self.root / ".ai-memory",
            workspace_dir=self.root / "workspace",
            phases_dir=self.root / ".ai-memory" / "phases",
        )
        self.config = AppConfig(
            project_root=self.root,
            paths=paths,
            agents={name: AgentConfig() for name in ("antigravity", "codex", "cursor")},
        )
        self.store = MemoryStore(paths.memory_dir, paths.phases_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_complete_workflow_creates_all_artifacts(self) -> None:
        manager = WorkflowManager(
            self.config,
            [AntigravityAgent(), CodexAgent(), CursorAgent()],
            self.store,
        )
        phase_dir = manager.run("Build authentication system")
        state = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))

        self.assertEqual("completed", state["status"])
        self.assertEqual(5, len(list((phase_dir / "tasks").glob("*.md"))))
        self.assertTrue((phase_dir / "prompt.md").is_file())
        phase_log = (phase_dir / "logs" / "workflow.log").read_text(encoding="utf-8")
        self.assertIn("Completed phase", phase_log)
        self.assertTrue(self.config.paths.workspace_dir.is_dir())
        for filename in MEMORY_FILES:
            self.assertTrue((self.config.paths.memory_dir / filename).is_file())

    def test_agent_failure_is_persisted(self) -> None:
        manager = WorkflowManager(
            self.config,
            [AntigravityAgent(), CodexAgent(), FailingAgent()],
            self.store,
        )
        with self.assertRaises(AgentExecutionError):
            manager.run("Trigger a failure")

        phase_dir = next(self.config.paths.phases_dir.iterdir())
        state = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", state["status"])
        review = next(step for step in state["steps"] if step["id"] == "review")
        self.assertEqual("failed", review["status"])
        self.assertIn("simulated integration failure", review["error"])

    def test_config_resolves_project_relative_paths(self) -> None:
        config_path = self.root / "config.yaml"
        config_path.write_text(
            "paths:\n  memory_dir: memory\n  workspace_dir: work\n  phases_dir: runs\n"
            "agents:\n  antigravity:\n    enabled: false\n",
            encoding="utf-8",
        )
        config = load_config(config_path, self.root)
        self.assertEqual(self.root / "memory", config.paths.memory_dir)
        self.assertEqual(self.root / "memory" / "runs", config.paths.phases_dir)
        self.assertFalse(config.agents["antigravity"].enabled)
        self.assertTrue(config.agents["codex"].enabled)


if __name__ == "__main__":
    unittest.main()
