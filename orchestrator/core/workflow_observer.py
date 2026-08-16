"""Optional lifecycle hooks used by interfaces layered over the workflow engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.models import ApprovalStatus, DashboardStatus


@dataclass(frozen=True, slots=True)
class ApprovalResolution:
    status: ApprovalStatus
    feedback: str = ""


class WorkflowObserver(Protocol):
    def on_transition(
        self,
        phase_id: str,
        status: DashboardStatus,
        current_agent: str | None,
        message: str,
        phase_dir: Path,
    ) -> None: ...

    def approval_resolution(self, phase_id: str, gate: str) -> ApprovalResolution: ...

    def request_approval(
        self,
        phase_id: str,
        gate: str,
        message: str,
        phase_dir: Path,
    ) -> None: ...

    def notify(self, phase_id: str, level: str, message: str) -> None: ...


class NullWorkflowObserver:
    """Preserve autonomous CLI behavior when no human interface is attached."""

    def on_transition(
        self,
        phase_id: str,
        status: DashboardStatus,
        current_agent: str | None,
        message: str,
        phase_dir: Path,
    ) -> None:
        return None

    def approval_resolution(self, phase_id: str, gate: str) -> ApprovalResolution:
        return ApprovalResolution(ApprovalStatus.APPROVED)

    def request_approval(
        self,
        phase_id: str,
        gate: str,
        message: str,
        phase_dir: Path,
    ) -> None:
        return None

    def notify(self, phase_id: str, level: str, message: str) -> None:
        return None
