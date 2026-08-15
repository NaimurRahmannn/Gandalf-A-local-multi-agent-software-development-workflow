"""Workflow coordination independent of CLI and concrete integrations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from orchestrator.agents.base import BaseAgent
from orchestrator.config import AppConfig
from orchestrator.exceptions import AgentExecutionError, WorkflowError
from orchestrator.memory import MemoryStore, utc_now
from orchestrator.models import AgentContext, AgentResult, PhaseStatus, StepStatus

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    id: str
    objective: str
    agent_name: str


DEFAULT_WORKFLOW: tuple[WorkflowStep, ...] = (
    WorkflowStep("planning", "Create the technical plan", "antigravity"),
    WorkflowStep("implementation", "Implement the approved plan", "codex"),
    WorkflowStep("review", "Review implementation and tests", "cursor"),
    WorkflowStep("final-review", "Make the final technical decision", "antigravity"),
    WorkflowStep("improvement", "Apply final requested improvements", "codex"),
)


class WorkflowManager:
    def __init__(
        self,
        config: AppConfig,
        agents: Sequence[BaseAgent],
        memory_store: MemoryStore,
        steps: Sequence[WorkflowStep] = DEFAULT_WORKFLOW,
    ) -> None:
        self.config = config
        self.agents = {agent.name: agent for agent in agents}
        self.memory_store = memory_store
        self.steps = tuple(steps)
        missing = {step.agent_name for step in steps} - self.agents.keys()
        if missing:
            raise WorkflowError(f"No agent implementation registered for: {', '.join(sorted(missing))}")

    def run(self, prompt: str) -> Path:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise WorkflowError("Phase prompt cannot be empty.")
        self.memory_store.ensure_structure(self.config.paths.workspace_dir)
        memory = self.memory_store.load_context()
        phase_id, phase_dir = self.memory_store.create_phase(clean_prompt)
        phase_log_handler = logging.FileHandler(
            phase_dir / "logs" / "workflow.log", encoding="utf-8"
        )
        phase_log_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
        phase_log_handler.setLevel(logging.INFO)
        previous_logger_level = LOGGER.level
        LOGGER.setLevel(logging.DEBUG)
        LOGGER.addHandler(phase_log_handler)
        state = self._initial_state(phase_id, clean_prompt)
        state_path = phase_dir / "status.json"
        self.memory_store.write_json_atomic(state_path, state)
        self.memory_store.append_progress(phase_id, PhaseStatus.RUNNING, clean_prompt)
        LOGGER.info("Started phase %s", phase_id)
        results: list[AgentResult] = []

        try:
            for order, step in enumerate(self.steps, start=1):
                step_state = state["steps"][order - 1]
                if not self.config.agents[step.agent_name].enabled:
                    step_state.update(status=StepStatus.SKIPPED, finished_at=utc_now())
                    self.memory_store.write_json_atomic(state_path, state)
                    LOGGER.warning("Skipped %s because %s is disabled", step.id, step.agent_name)
                    continue
                step_state.update(status=StepStatus.RUNNING, started_at=utc_now())
                self.memory_store.write_json_atomic(state_path, state)
                LOGGER.info("Executing step %s with %s", step.id, step.agent_name)
                context = AgentContext(
                    phase_id=phase_id,
                    phase_prompt=clean_prompt,
                    step_id=step.id,
                    objective=step.objective,
                    project_root=self.config.project_root,
                    workspace_dir=self.config.paths.workspace_dir,
                    phase_dir=phase_dir,
                    memory=memory,
                    prior_results=tuple(results),
                )
                try:
                    result = self.agents[step.agent_name].execute(context)
                except Exception as exc:
                    raise AgentExecutionError(
                        f"Agent '{step.agent_name}' failed during step '{step.id}': {exc}"
                    ) from exc
                result_path = self.memory_store.write_result(phase_dir, order, result)
                results.append(result)
                step_state.update(
                    status=StepStatus.COMPLETED,
                    finished_at=utc_now(),
                    output=str(result_path.relative_to(phase_dir)),
                )
                self.memory_store.write_json_atomic(state_path, state)

        except KeyboardInterrupt:
            self._finish(state, state_path, PhaseStatus.INTERRUPTED, "Interrupted by user")
            self.memory_store.append_progress(phase_id, PhaseStatus.INTERRUPTED, "Interrupted by user")
            LOGGER.warning("Phase %s was interrupted", phase_id)
            self._close_log_handler(phase_log_handler, previous_logger_level)
            raise
        except Exception as exc:
            running = next((item for item in state["steps"] if item["status"] == StepStatus.RUNNING), None)
            if running is not None:
                running.update(status=StepStatus.FAILED, finished_at=utc_now(), error=str(exc))
            self._finish(state, state_path, PhaseStatus.FAILED, str(exc))
            self.memory_store.append_progress(phase_id, PhaseStatus.FAILED, str(exc))
            LOGGER.exception("Phase %s failed", phase_id)
            self._close_log_handler(phase_log_handler, previous_logger_level)
            raise

        self._finish(state, state_path, PhaseStatus.COMPLETED)
        self.memory_store.append_progress(phase_id, PhaseStatus.COMPLETED, "Workflow completed")
        LOGGER.info("Completed phase %s", phase_id)
        self._close_log_handler(phase_log_handler, previous_logger_level)
        return phase_dir

    @staticmethod
    def _close_log_handler(handler: logging.FileHandler, previous_level: int) -> None:
        LOGGER.removeHandler(handler)
        handler.close()
        LOGGER.setLevel(previous_level)

    def _initial_state(self, phase_id: str, prompt: str) -> dict[str, object]:
        return {
            "phase_id": phase_id,
            "prompt": prompt,
            "status": PhaseStatus.RUNNING,
            "created_at": utc_now(),
            "finished_at": None,
            "error": None,
            "steps": [
                {
                    "id": step.id,
                    "agent": step.agent_name,
                    "objective": step.objective,
                    "status": StepStatus.PENDING,
                    "started_at": None,
                    "finished_at": None,
                    "output": None,
                    "error": None,
                }
                for step in self.steps
            ],
        }

    def _finish(
        self,
        state: dict[str, object],
        state_path: Path,
        status: PhaseStatus,
        error: str | None = None,
    ) -> None:
        state.update(status=status, finished_at=utc_now(), error=error)
        self.memory_store.write_json_atomic(state_path, state)
