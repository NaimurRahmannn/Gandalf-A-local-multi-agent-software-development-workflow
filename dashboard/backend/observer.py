"""Bridge orchestrator lifecycle hooks into the dashboard database."""

from __future__ import annotations

from pathlib import Path

from dashboard.backend.database import DashboardDatabase
from orchestrator.core.workflow_observer import ApprovalResolution
from orchestrator.models import ApprovalStatus, DashboardStatus


class DashboardWorkflowObserver:
    def __init__(self, database: DashboardDatabase, dashboard_phase_id: str) -> None:
        self.database = database
        self.dashboard_phase_id = dashboard_phase_id

    def on_transition(
        self,
        phase_id: str,
        status: DashboardStatus,
        current_agent: str | None,
        message: str,
        phase_dir: Path,
    ) -> None:
        self.database.bind_orchestrator_phase(self.dashboard_phase_id, phase_id, phase_dir)
        self.database.transition(
            self.dashboard_phase_id,
            status,
            current_agent,
            message,
            message if status == DashboardStatus.FAILED else None,
        )

    def approval_resolution(self, phase_id: str, gate: str) -> ApprovalResolution:
        approval = self.database.get_approval(self.dashboard_phase_id, gate)
        if approval is None:
            return ApprovalResolution(ApprovalStatus.PENDING)
        return ApprovalResolution(
            ApprovalStatus(approval["status"]), str(approval.get("feedback") or "")
        )

    def request_approval(
        self,
        phase_id: str,
        gate: str,
        message: str,
        phase_dir: Path,
    ) -> None:
        self.database.request_approval(self.dashboard_phase_id, gate)

    def notify(self, phase_id: str, level: str, message: str) -> None:
        phase = self.database.get_phase(self.dashboard_phase_id)
        if phase:
            self.database.add_notification(
                phase["project_id"], self.dashboard_phase_id, level, message
            )
