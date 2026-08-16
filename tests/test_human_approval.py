from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path
from uuid import uuid4

from tests.test_workflow import TeamAgent

from orchestrator.config import AgentConfig, AppConfig, GitConfig, PathsConfig
from orchestrator.core.cli_runner import CliRunner
from orchestrator.core.git_manager import GitManager
from orchestrator.core.test_runner import TestRunner
from orchestrator.core.workflow_observer import ApprovalResolution
from orchestrator.memory import MemoryStore
from orchestrator.models import ApprovalStatus, DashboardStatus
from orchestrator.workflow import WorkflowManager


class GatedObserver:
    def __init__(self) -> None:
        self.resolutions: dict[str, ApprovalResolution] = {}
        self.requests: list[str] = []
        self.transitions: list[DashboardStatus] = []

    def on_transition(self, phase_id, status, current_agent, message, phase_dir) -> None:
        self.transitions.append(status)

    def approval_resolution(self, phase_id: str, gate: str) -> ApprovalResolution:
        return self.resolutions.get(gate, ApprovalResolution(ApprovalStatus.PENDING))

    def request_approval(self, phase_id, gate, message, phase_dir) -> None:
        if gate not in self.requests:
            self.requests.append(gate)

    def notify(self, phase_id, level, message) -> None:
        return None


class HumanApprovalWorkflowTests(unittest.TestCase):
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
        prompts = Path(__file__).resolve().parents[1] / "orchestrator" / "prompts"
        paths = PathsConfig(
            self.root / ".ai-memory",
            self.workspace,
            self.root / ".ai-memory" / "phases",
            prompts,
        )
        agents = {
            name: AgentConfig(True, name, (), 10)
            for name in ("antigravity", "codex", "cursor")
        }
        self.config = AppConfig(
            self.root,
            paths,
            agents,
            git=GitConfig(allow_commit=True, commit_message="Approve {phase_id}"),
        )
        self.observer = GatedObserver()
        runner = CliRunner()
        self.manager = WorkflowManager(
            self.config,
            [TeamAgent("antigravity"), TeamAgent("codex"), TeamAgent("cursor")],
            MemoryStore(paths.memory_dir, paths.phases_dir),
            GitManager(runner, allow_commit=True),
            TestRunner(runner, (), 10),
            self.observer,
        )

    def tearDown(self) -> None:
        def remove_readonly(function, path, _error):
            os.chmod(path, stat.S_IWRITE)
            function(path)

        shutil.rmtree(self.root, onexc=remove_readonly)

    def test_final_changes_and_commit_are_separate_durable_gates(self) -> None:
        phase_dir = self.manager.run("Build gated feature")
        state = self._state(phase_dir)
        self.assertEqual("waiting_approval", state["status"])
        self.assertEqual("human_approval", state["next_action"])
        self.assertEqual(["final-changes-cycle-1"], self.observer.requests)

        self.observer.resolutions["final-changes-cycle-1"] = ApprovalResolution(
            ApprovalStatus.APPROVED, "Implementation accepted"
        )
        self.manager.resume(state["phase_id"])
        state = self._state(phase_dir)
        self.assertEqual("commit_approval", state["next_action"])
        self.assertEqual(
            ["final-changes-cycle-1", "git-commit"], self.observer.requests
        )

        self.observer.resolutions["git-commit"] = ApprovalResolution(
            ApprovalStatus.APPROVED, "Commit accepted"
        )
        self.manager.resume(state["phase_id"])
        state = self._state(phase_dir)
        self.assertEqual("completed", state["status"])
        self.assertTrue(state["commit"])
        self.assertIn(DashboardStatus.WAITING_APPROVAL, self.observer.transitions)
        self.assertEqual(DashboardStatus.COMPLETED, self.observer.transitions[-1])

    @staticmethod
    def _state(phase_dir: Path) -> dict:
        return json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
