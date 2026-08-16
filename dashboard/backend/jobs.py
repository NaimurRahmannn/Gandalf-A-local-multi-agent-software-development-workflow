"""Project management and durable background workflow execution."""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from dashboard.backend.database import DashboardDatabase
from dashboard.backend.observer import DashboardWorkflowObserver
from dashboard.backend.settings import DashboardSettings
from orchestrator.agents import build_agents
from orchestrator.config import PathsConfig, load_config
from orchestrator.core.cli_runner import CliRunner
from orchestrator.core.git_manager import GitManager
from orchestrator.core.test_runner import TestRunner
from orchestrator.memory import MemoryStore
from orchestrator.models import ApprovalStatus, DashboardStatus
from orchestrator.workflow import WorkflowManager

LOGGER = logging.getLogger(__name__)
TERMINAL_STATUSES = {DashboardStatus.COMPLETED, DashboardStatus.FAILED}


class DashboardServiceError(RuntimeError):
    pass


class DashboardJobService:
    def __init__(self, settings: DashboardSettings, database: DashboardDatabase) -> None:
        self.settings = settings
        self.database = database
        self.executor = ThreadPoolExecutor(
            max_workers=settings.workers, thread_name_prefix="ai-team-phase"
        )
        self._lock = threading.Lock()
        self._jobs: dict[str, Future[None]] = {}

    def create_project(self, name: str) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise DashboardServiceError("Project name cannot be empty.")
        slug = re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")[:60]
        if not slug:
            raise DashboardServiceError("Project name must contain a letter or number.")
        root = (self.settings.projects_root / slug).resolve()
        try:
            root.relative_to(self.settings.projects_root.resolve())
        except ValueError as exc:
            raise DashboardServiceError("Resolved project path is outside projects_root.") from exc
        root.mkdir(parents=True, exist_ok=True)
        memory = root / ".ai-memory"
        store = MemoryStore(memory, memory / "phases")
        store.ensure_structure(root)
        if not (root / ".git").is_dir():
            runner = CliRunner()
            runner.run(
                ("git", "init", "--quiet"),
                cwd=root,
                log_path=memory / "project-setup.log",
                timeout_seconds=30,
            )
        self._exclude_runtime_memory(root)
        try:
            return self.database.create_project(clean_name, slug, root, memory)
        except Exception as exc:
            raise DashboardServiceError(f"Unable to register project '{clean_name}': {exc}") from exc

    def start_phase(self, project_id: str, prompt: str) -> dict[str, Any]:
        project = self.database.get_project(project_id)
        if project is None:
            raise DashboardServiceError(f"Project not found: {project_id}")
        if not prompt.strip():
            raise DashboardServiceError("Phase prompt cannot be empty.")
        phase = self.database.create_phase(project_id, prompt.strip())
        self.submit(phase["id"], resume=False)
        return phase

    def resume_phase(self, dashboard_phase_id: str) -> dict[str, Any]:
        phase = self.database.get_phase(dashboard_phase_id)
        if phase is None:
            raise DashboardServiceError("Phase not found.")
        if phase["status"] != DashboardStatus.FAILED:
            raise DashboardServiceError("Only failed phases can be resumed.")
        if not phase.get("orchestrator_phase_id") or not phase.get("phase_dir"):
            raise DashboardServiceError(
                "This phase failed before resumable workflow state was created."
            )
        state_path = Path(phase["phase_dir"]) / "status.json"
        if not state_path.is_file():
            raise DashboardServiceError(
                f"Persisted workflow state is missing: {state_path}"
            )
        if not self.database.reopen_failed_phase(dashboard_phase_id):
            raise DashboardServiceError("Phase is no longer in a resumable failed state.")
        self.database.add_notification(
            phase["project_id"],
            dashboard_phase_id,
            "info",
            "Phase resume requested; completed steps will not be replayed",
        )
        if not self.submit(dashboard_phase_id, resume=True):
            raise DashboardServiceError("Phase resume is already running.")
        return self.database.get_phase(dashboard_phase_id) or phase

    def submit(self, dashboard_phase_id: str, *, resume: bool) -> bool:
        with self._lock:
            current = self._jobs.get(dashboard_phase_id)
            if current and not current.done():
                return False
            future = self.executor.submit(self._run, dashboard_phase_id, resume)
            self._jobs[dashboard_phase_id] = future
            future.add_done_callback(
                lambda _future, phase_id=dashboard_phase_id: self._forget(phase_id)
            )
            return True

    def _forget(self, phase_id: str) -> None:
        with self._lock:
            self._jobs.pop(phase_id, None)

    def _run(self, dashboard_phase_id: str, resume: bool) -> None:
        phase = self.database.get_phase(dashboard_phase_id)
        if phase is None:
            return
        project = self.database.get_project(phase["project_id"])
        if project is None:
            self.database.transition(
                dashboard_phase_id, DashboardStatus.FAILED, None, "Project record is missing"
            )
            return
        try:
            manager = self._build_manager(project, dashboard_phase_id)
            if resume:
                orchestrator_id = phase.get("orchestrator_phase_id")
                if not orchestrator_id:
                    raise DashboardServiceError("Phase has no persisted orchestrator identifier.")
                manager.resume(orchestrator_id)
            else:
                manager.run(phase["prompt"])
        except Exception as exc:
            LOGGER.exception("Dashboard phase %s failed", dashboard_phase_id)
            current = self.database.get_phase(dashboard_phase_id)
            if current is None or current["status"] != DashboardStatus.FAILED:
                self.database.transition(
                    dashboard_phase_id, DashboardStatus.FAILED, None, str(exc), str(exc)
                )
                self.database.add_notification(
                    phase["project_id"], dashboard_phase_id, "error", f"Phase failed: {exc}"
                )

    def _build_manager(
        self, project: dict[str, Any], dashboard_phase_id: str
    ) -> WorkflowManager:
        base = load_config(self.settings.orchestrator_config, self.settings.repository_root)
        root = Path(project["root_path"]).resolve()
        memory = Path(project["memory_path"]).resolve()
        paths = PathsConfig(memory, root, memory / "phases", base.paths.prompts_dir)
        config = replace(base, project_root=root, paths=paths)
        runner = CliRunner()
        return WorkflowManager(
            config,
            build_agents(config, runner),
            MemoryStore(memory, memory / "phases"),
            GitManager(runner, allow_commit=config.git.allow_commit),
            TestRunner(runner, config.checks.commands, config.checks.timeout_seconds),
            DashboardWorkflowObserver(self.database, dashboard_phase_id),
        )

    def resolve_approval(
        self,
        dashboard_phase_id: str,
        status: ApprovalStatus,
        feedback: str,
    ) -> dict[str, Any]:
        phase = self.database.get_phase(dashboard_phase_id)
        if phase is None:
            raise DashboardServiceError("Phase not found.")
        approvals = self.database.list_approvals(dashboard_phase_id)
        pending = next((item for item in reversed(approvals) if item["status"] == "pending"), None)
        if pending is None:
            raise DashboardServiceError("This phase has no pending approval request.")
        resolution = ApprovalStatus(status)
        if not self.database.resolve_approval(
            dashboard_phase_id, pending["gate"], resolution, feedback.strip()
        ):
            raise DashboardServiceError("Approval was already resolved.")
        self.database.add_notification(
            phase["project_id"],
            dashboard_phase_id,
            "info",
            f"Human decision: {resolution}",
        )
        self.submit(dashboard_phase_id, resume=True)
        return self.database.get_phase(dashboard_phase_id) or phase

    def recover(self) -> None:
        for phase in self.database.list_phases():
            status = DashboardStatus(phase["status"])
            if status in TERMINAL_STATUSES or status == DashboardStatus.WAITING_APPROVAL:
                continue
            self.submit(phase["id"], resume=bool(phase.get("orchestrator_phase_id")))

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _exclude_runtime_memory(root: Path) -> None:
        """Keep dashboard state out of project diffs and approved source commits."""
        exclude = root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if ".ai-memory/" not in current.splitlines():
            separator = "" if not current or current.endswith("\n") else "\n"
            exclude.write_text(f"{current}{separator}.ai-memory/\n", encoding="utf-8")
