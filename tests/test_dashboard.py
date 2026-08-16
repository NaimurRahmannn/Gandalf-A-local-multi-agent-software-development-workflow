from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from dashboard.backend.app import create_app
from dashboard.backend.database import DashboardDatabase
from dashboard.backend.settings import DashboardSettings
from orchestrator.memory import MemoryStore
from orchestrator.models import ApprovalStatus, DashboardStatus


class FakeJobService:
    def __init__(self, database: DashboardDatabase, projects_root: Path) -> None:
        self.database = database
        self.projects_root = projects_root
        self.resumed: list[str] = []

    def recover(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def create_project(self, name: str) -> dict[str, Any]:
        slug = name.lower().replace(" ", "-")
        root = self.projects_root / slug
        memory = root / ".ai-memory"
        MemoryStore(memory, memory / "phases").ensure_structure(root)
        return self.database.create_project(name, slug, root, memory)

    def start_phase(self, project_id: str, prompt: str) -> dict[str, Any]:
        if self.database.get_project(project_id) is None:
            raise RuntimeError("Project not found")
        return self.database.create_phase(project_id, prompt)

    def resolve_approval(
        self, phase_id: str, resolution: ApprovalStatus, feedback: str
    ) -> dict[str, Any]:
        pending = next(
            item
            for item in reversed(self.database.list_approvals(phase_id))
            if item["status"] == ApprovalStatus.PENDING
        )
        if not self.database.resolve_approval(
            phase_id, pending["gate"], resolution, feedback
        ):
            raise RuntimeError("Approval already resolved")
        self.resumed.append(phase_id)
        return self.database.get_phase(phase_id) or {}


class DashboardDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "runtime" / uuid4().hex
        self.root.mkdir(parents=True)
        self.db = DashboardDatabase(self.root / "dashboard.db")
        self.db.initialize()

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_state_events_approvals_and_notifications_are_durable(self) -> None:
        project = self.db.create_project(
            "Atlas", "atlas", self.root / "atlas", self.root / "atlas" / ".ai-memory"
        )
        phase = self.db.create_phase(project["id"], "Build authentication")
        self.db.transition(
            phase["id"], DashboardStatus.REVIEWING, "cursor", "Reviewing changes"
        )
        approval = self.db.request_approval(phase["id"], "final-changes-cycle-1")
        self.assertEqual("pending", approval["status"])
        self.assertTrue(
            self.db.resolve_approval(
                phase["id"], "final-changes-cycle-1", ApprovalStatus.APPROVED, "Ship it"
            )
        )
        self.db.add_notification(project["id"], phase["id"], "warning", "Approval required")

        reopened = DashboardDatabase(self.db.path)
        reopened.initialize()
        self.assertEqual("REVIEWING", reopened.get_phase(phase["id"])["status"])
        self.assertEqual("cursor", reopened.list_events(phase["id"])[-1]["current_agent"])
        self.assertEqual("approved", reopened.list_approvals(phase["id"])[0]["status"])
        notice = reopened.list_notifications()[0]
        self.assertTrue(reopened.mark_notification_read(notice["id"]))


class DashboardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "runtime" / uuid4().hex
        self.root.mkdir(parents=True)
        repository = Path(__file__).resolve().parents[1]
        self.db = DashboardDatabase(self.root / "dashboard.db")
        self.jobs = FakeJobService(self.db, self.root / "projects")
        settings = DashboardSettings(
            repository_root=repository,
            database_path=self.db.path,
            projects_root=self.root / "projects",
            orchestrator_config=repository / "orchestrator" / "config.yaml",
            frontend_dir=repository / "dashboard" / "frontend",
            password="test-password",
            session_secret="test-secret",
        )
        self.client_context = TestClient(create_app(settings, self.db, self.jobs))
        self.client = self.client_context.__enter__()
        self.auth = ("admin", "test-password")

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        shutil.rmtree(self.root)

    def test_projects_phases_approval_logs_and_sse(self) -> None:
        self.assertEqual(401, self.client.get("/projects").status_code)
        project_response = self.client.post(
            "/projects", auth=self.auth, json={"name": "Project Atlas"}
        )
        self.assertEqual(201, project_response.status_code)
        project = project_response.json()

        phase_response = self.client.post(
            "/phases/start",
            auth=self.auth,
            json={"project_id": project["id"], "prompt": "Build authentication"},
        )
        self.assertEqual(202, phase_response.status_code)
        phase = phase_response.json()
        phase_dir = self.root / "phase-artifacts"
        (phase_dir / "logs").mkdir(parents=True)
        (phase_dir / "plan.md").write_text("# Plan\n", encoding="utf-8")
        (phase_dir / "logs" / "agent.log").write_text("running\n", encoding="utf-8")
        self.db.bind_orchestrator_phase(phase["id"], "phase-001", phase_dir)
        self.db.transition(
            phase["id"], DashboardStatus.WAITING_APPROVAL, None, "Approval required"
        )
        self.db.request_approval(phase["id"], "final-changes-cycle-1")

        detail = self.client.get(f"/phases/{phase['id']}", auth=self.auth).json()
        self.assertEqual("# Plan", detail["artifacts"]["plan.md"].strip())
        logs = self.client.get(f"/logs/{phase['id']}", auth=self.auth).json()
        self.assertEqual("running", logs["agent.log"].strip())
        stream = self.client.get(
            f"/events/{phase['id']}?once=true", auth=self.auth
        )
        self.assertEqual(200, stream.status_code)
        self.assertIn("event: phase", stream.text)

        approval = self.client.post(
            f"/phases/{phase['id']}/approve",
            auth=self.auth,
            json={"feedback": "Looks good"},
        )
        self.assertEqual(200, approval.status_code)
        self.assertEqual([phase["id"]], self.jobs.resumed)
        self.assertEqual(
            "approved", self.db.list_approvals(phase["id"])[0]["status"]
        )

    def test_request_changes_requires_feedback(self) -> None:
        project = self.client.post(
            "/projects", auth=self.auth, json={"name": "Feedback Project"}
        ).json()
        phase = self.client.post(
            "/phases/start",
            auth=self.auth,
            json={"project_id": project["id"], "prompt": "Review this"},
        ).json()
        self.db.request_approval(phase["id"], "final-changes-cycle-1")
        response = self.client.post(
            f"/phases/{phase['id']}/request-changes",
            auth=self.auth,
            json={"feedback": ""},
        )
        self.assertEqual(422, response.status_code)


if __name__ == "__main__":
    unittest.main()
