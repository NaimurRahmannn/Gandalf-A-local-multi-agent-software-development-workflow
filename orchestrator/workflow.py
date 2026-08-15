"""Resumable Git-aware workflow coordination for the local AI team."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Sequence

from orchestrator.agents.base import BaseAgent
from orchestrator.config import AppConfig
from orchestrator.core.git_manager import GitManager
from orchestrator.core.test_runner import TestRunner
from orchestrator.exceptions import AgentExecutionError, ResumeError, WorkflowError
from orchestrator.memory import MemoryStore, utc_now
from orchestrator.models import AgentContext, AgentResult, PhaseStatus, StepStatus

LOGGER = logging.getLogger(__name__)
DECISION_PATTERN = re.compile(
    r"^\s*REVIEW_DECISION\s*:\s*(APPROVED|CHANGES_REQUIRED)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class WorkflowManager:
    """Run and resume the phase state machine without replaying completed steps."""

    def __init__(
        self,
        config: AppConfig,
        agents: Sequence[BaseAgent],
        memory_store: MemoryStore,
        git_manager: GitManager,
        test_runner: TestRunner,
    ) -> None:
        self.config = config
        self.agents = {agent.name: agent for agent in agents}
        self.memory_store = memory_store
        self.git_manager = git_manager
        self.test_runner = test_runner
        missing = {"antigravity", "codex", "cursor"} - self.agents.keys()
        if missing:
            raise WorkflowError(f"No agent implementation registered for: {', '.join(sorted(missing))}")

    def run(self, prompt: str) -> Path:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise WorkflowError("Phase prompt cannot be empty.")
        self.memory_store.ensure_structure(self.config.paths.workspace_dir)
        phase_id, phase_dir = self.memory_store.create_phase(clean_prompt)
        state = self._initial_state(phase_id, clean_prompt)
        self.memory_store.write_json_atomic(phase_dir / "status.json", state)
        self.memory_store.append_progress(phase_id, PhaseStatus.RUNNING, clean_prompt)
        return self._execute(phase_dir, state, self.memory_store.load_context(), [])

    def resume(self, phase_id: str) -> Path:
        self.memory_store.ensure_structure(self.config.paths.workspace_dir)
        try:
            phase_dir = self.memory_store.get_phase(phase_id)
            state = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ResumeError(f"Unable to load phase '{phase_id}': {exc}") from exc
        if state.get("version") != 3 or "next_action" not in state:
            raise ResumeError("Only Phase 3 workflow runs can be resumed.")
        if state.get("status") in {PhaseStatus.COMPLETED, PhaseStatus.NEEDS_ATTENTION}:
            raise ResumeError(f"Phase '{phase_id}' is already terminal: {state.get('status')}")
        state.update(status=PhaseStatus.RUNNING, error=None, resumed_at=utc_now())
        self.memory_store.write_json_atomic(phase_dir / "status.json", state)
        self.memory_store.append_progress(phase_id, PhaseStatus.RUNNING, "Workflow resumed")
        return self._execute(
            phase_dir,
            state,
            self.memory_store.load_context(),
            self.memory_store.load_handoffs(phase_dir),
        )

    def _execute(
        self,
        phase_dir: Path,
        state: dict[str, Any],
        memory: dict[str, str],
        results: list[AgentResult],
    ) -> Path:
        state_path = phase_dir / "status.json"
        handler, previous_level = self._attach_phase_log(phase_dir)
        LOGGER.info("Running phase %s from action %s", state["phase_id"], state["next_action"])
        try:
            while state["next_action"] != "finalize":
                action = state["next_action"]
                cycle = int(state["review_cycle"])
                if action == "planning":
                    self._execute_agent(
                        state, state_path, phase_dir, memory, results,
                        "planning", "Create the technical plan", "antigravity",
                    )
                    self._advance(state, state_path, "implementation")
                elif action == "implementation":
                    self._ensure_before_snapshot(phase_dir)
                    self._execute_agent(
                        state, state_path, phase_dir, memory, results,
                        "implementation", "Implement the approved plan", "codex",
                    )
                    self._collect_git_snapshot(phase_dir)
                    self._advance(state, state_path, "tests")
                elif action == "tests":
                    self._execute_tests(state, state_path, phase_dir, results, cycle)
                    self._advance(state, state_path, "review")
                elif action == "review":
                    self._execute_agent(
                        state, state_path, phase_dir, memory, results,
                        f"review-cycle-{cycle}",
                        f"Review code changes and tests for cycle {cycle}",
                        "cursor",
                    )
                    self._advance(state, state_path, "architecture_review")
                elif action == "architecture_review":
                    decision_result = self._execute_agent(
                        state, state_path, phase_dir, memory, results,
                        f"architecture-review-cycle-{cycle}",
                        "Decide whether implementation matches the goal, architecture, and test bar",
                        "antigravity",
                    )
                    approved = self._is_approved(decision_result.content)
                    state["approved"] = approved
                    state["review_decision"] = "APPROVED" if approved else "CHANGES_REQUIRED"
                    if approved or cycle >= self.config.workflow.max_review_cycles:
                        if not approved:
                            state["remaining_issues"] = decision_result.content
                        self._advance(state, state_path, "finalize")
                    else:
                        self._advance(state, state_path, "improvement")
                elif action == "improvement":
                    self._execute_agent(
                        state, state_path, phase_dir, memory, results,
                        f"improvement-cycle-{cycle}",
                        f"Apply required fixes from review cycle {cycle}",
                        "codex",
                    )
                    self._collect_git_snapshot(phase_dir)
                    state["review_cycle"] = cycle + 1
                    self._advance(state, state_path, "tests")
                else:
                    raise WorkflowError(f"Unknown workflow action in phase state: {action}")

            self._finalize(phase_dir, state, state_path, results)
            return phase_dir
        except KeyboardInterrupt:
            self._mark_running_step(state, StepStatus.FAILED, "Interrupted by user")
            self._finish(state, state_path, PhaseStatus.INTERRUPTED, "Interrupted by user")
            self.memory_store.append_progress(
                state["phase_id"], PhaseStatus.INTERRUPTED, "Interrupted by user; phase can be resumed"
            )
            LOGGER.warning("Phase %s was interrupted and can be resumed", state["phase_id"])
            raise
        except Exception as exc:
            self._mark_running_step(state, StepStatus.FAILED, str(exc))
            self._finish(state, state_path, PhaseStatus.FAILED, str(exc))
            self.memory_store.append_progress(
                state["phase_id"], PhaseStatus.FAILED, f"{exc}; phase can be resumed"
            )
            self._write_failure_report(phase_dir, state, results, str(exc))
            LOGGER.exception("Phase %s failed at %s", state["phase_id"], state["next_action"])
            raise
        finally:
            self._close_phase_log(handler, previous_level)

    def _execute_agent(
        self,
        state: dict[str, Any],
        state_path: Path,
        phase_dir: Path,
        memory: dict[str, str],
        results: list[AgentResult],
        step_id: str,
        objective: str,
        agent_name: str,
    ) -> AgentResult:
        existing_step = next((item for item in state["steps"] if item["id"] == step_id), None)
        if existing_step and existing_step["status"] == StepStatus.COMPLETED:
            existing_result = next((item for item in results if item.step_id == step_id), None)
            if existing_result is None:
                raise ResumeError(f"Completed step '{step_id}' has no persisted handoff.")
            return existing_result

        step = existing_step or {
            "id": step_id,
            "agent": agent_name,
            "objective": objective,
            "status": StepStatus.PENDING,
            "started_at": None,
            "finished_at": None,
            "output": None,
            "error": None,
        }
        if existing_step is None:
            state["steps"].append(step)
        step.update(status=StepStatus.RUNNING, started_at=utc_now(), finished_at=None, error=None)
        self.memory_store.write_json_atomic(state_path, state)
        LOGGER.info("Executing %s with %s", step_id, agent_name)

        if not self.config.agents[agent_name].enabled:
            result = AgentResult(agent_name, step_id, f"{agent_name} disabled", "Agent disabled in config.")
            step_status = StepStatus.SKIPPED
        else:
            context = AgentContext(
                phase_id=state["phase_id"],
                phase_prompt=state["prompt"],
                step_id=step_id,
                objective=objective,
                project_root=self.config.project_root,
                workspace_dir=self.config.paths.workspace_dir,
                phase_dir=phase_dir,
                memory=memory,
                prior_results=tuple(results),
            )
            try:
                result = self.agents[agent_name].execute(context)
            except Exception as exc:
                raise AgentExecutionError(
                    f"Agent '{agent_name}' failed during step '{step_id}': {exc}"
                ) from exc
            step_status = StepStatus.COMPLETED

        result_path = self.memory_store.write_result(
            phase_dir, state["steps"].index(step) + 1, result
        )
        results.append(result)
        self.memory_store.write_handoffs(phase_dir, results)
        step.update(
            status=step_status,
            finished_at=utc_now(),
            output=str(result_path.relative_to(phase_dir)),
        )
        self.memory_store.write_json_atomic(state_path, state)
        return result

    def _execute_tests(
        self,
        state: dict[str, Any],
        state_path: Path,
        phase_dir: Path,
        results: list[AgentResult],
        cycle: int,
    ) -> None:
        step_id = f"tests-cycle-{cycle}"
        existing = next((item for item in state["steps"] if item["id"] == step_id), None)
        if existing and existing["status"] == StepStatus.COMPLETED:
            return
        step = existing or {
            "id": step_id,
            "agent": "test-runner",
            "objective": f"Run configured checks for cycle {cycle}",
            "status": StepStatus.PENDING,
            "started_at": None,
            "finished_at": None,
            "output": "test-results.md",
            "error": None,
        }
        if existing is None:
            state["steps"].append(step)
        step.update(status=StepStatus.RUNNING, started_at=utc_now(), error=None)
        self.memory_store.write_json_atomic(state_path, state)
        suite = self.test_runner.run(self.config.paths.workspace_dir, phase_dir, cycle)
        markdown = self.test_runner.to_markdown(suite)
        result = AgentResult(
            "test-runner",
            step_id,
            f"Test verification cycle {cycle}",
            markdown,
            {"passed": str(suite.passed).lower()},
        )
        results.append(result)
        self.memory_store.write_handoffs(phase_dir, results)
        step.update(status=StepStatus.COMPLETED, finished_at=utc_now())
        self.memory_store.write_json_atomic(state_path, state)

    def _ensure_before_snapshot(self, phase_dir: Path) -> None:
        output = phase_dir / "before-state.txt"
        if output.stat().st_size:
            return
        self.git_manager.create_checkpoint(
            self.config.paths.workspace_dir,
            phase_dir / "logs" / "git-manager.log",
            output,
            label="before implementation",
        )

    def _collect_git_snapshot(self, phase_dir: Path) -> tuple[str, tuple[str, ...]]:
        log_path = phase_dir / "logs" / "git-manager.log"
        diff = self.git_manager.get_diff(self.config.paths.workspace_dir, log_path)
        changed = self.git_manager.get_changed_files(self.config.paths.workspace_dir, log_path)
        header = "# Changed files\n" + ("\n".join(changed) if changed else "None")
        (phase_dir / "changes.diff").write_text(
            f"{header}\n\n# Git diff\n{diff or 'No tracked diff.'}\n", encoding="utf-8"
        )
        return diff, changed

    def _finalize(
        self,
        phase_dir: Path,
        state: dict[str, Any],
        state_path: Path,
        results: list[AgentResult],
    ) -> None:
        _, changed = self._collect_git_snapshot(phase_dir)
        self.git_manager.create_checkpoint(
            self.config.paths.workspace_dir,
            phase_dir / "logs" / "git-manager.log",
            phase_dir / "after-state.txt",
            label="after workflow",
        )
        approved = bool(state.get("approved"))
        status = (
            PhaseStatus.COMPLETED
            if approved or not self.config.workflow.require_approval
            else PhaseStatus.NEEDS_ATTENTION
        )
        commit = None
        if self.config.git.allow_commit:
            message = self.config.git.commit_message.format(phase_id=state["phase_id"])
            commit = self.git_manager.create_commit(
                self.config.paths.workspace_dir,
                phase_dir / "logs" / "git-manager.log",
                message,
            )
            state["commit"] = commit or None
        state["status"] = status
        self._write_phase_report(phase_dir, state, results, changed, commit)
        decision = str(state.get("review_decision", "CHANGES_REQUIRED"))
        self.memory_store.update_project_memory(state["phase_id"], status, changed, decision)
        self._finish(state, state_path, status)
        self.memory_store.append_progress(
            state["phase_id"], status, f"Workflow finished with decision {decision}"
        )
        LOGGER.info("Phase %s finished with status %s", state["phase_id"], status)

    def _write_phase_report(
        self,
        phase_dir: Path,
        state: dict[str, Any],
        results: list[AgentResult],
        changed_files: tuple[str, ...],
        commit: str | None,
    ) -> None:
        reviews = [item for item in results if "review" in item.step_id]
        tests = [item for item in results if item.step_id.startswith("tests-cycle-")]
        implementations = [
            item
            for item in results
            if item.step_id == "implementation" or item.step_id.startswith("improvement-cycle-")
        ]
        files = "\n".join(f"- `{name}`" for name in changed_files) or "- None"
        implementation_text = "\n\n".join(
            f"### {item.summary}\n\n{item.content}" for item in implementations
        ) or "No implementation handoff recorded."
        review_text = "\n\n".join(
            f"### {item.summary}\n\n{item.content}" for item in reviews
        ) or "No reviews recorded."
        test_text = "\n\n".join(item.content for item in tests) or "No tests recorded."
        remaining = state.get("remaining_issues") or "None reported."
        recommendations = (
            "Proceed with normal human verification."
            if state.get("approved")
            else "Resolve remaining review issues, then start a follow-up phase."
        )
        (phase_dir / "phase-report.md").write_text(
            f"# Phase Report\n\n"
            f"## Goal\n\n{state['prompt']}\n\n"
            f"## Outcome\n\n- Status: {state['status']}\n"
            f"- Review decision: {state.get('review_decision', 'not reached')}\n"
            f"- Review cycles: {state['review_cycle']}\n"
            f"- Commit: {commit or 'Not created'}\n\n"
            f"## Changes made\n\n{implementation_text}\n\n"
            f"## Files modified\n\n{files}\n\n"
            f"## Reviews\n\n{review_text}\n\n"
            f"## Tests\n\n{test_text}\n\n"
            f"## Remaining issues\n\n{remaining}\n\n"
            f"## Recommendations\n\n{recommendations}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_failure_report(
        phase_dir: Path,
        state: dict[str, Any],
        results: list[AgentResult],
        error: str,
    ) -> None:
        handoffs = "\n".join(f"- {item.step_id}: {item.summary}" for item in results) or "- None"
        (phase_dir / "phase-report.md").write_text(
            f"# Phase Report\n\n## Goal\n\n{state['prompt']}\n\n"
            f"## Outcome\n\n- Status: {state['status']}\n- Error: {error}\n"
            f"- Resume action: `{state['next_action']}`\n\n"
            f"## Completed handoffs\n\n{handoffs}\n\n"
            "## Recommendations\n\nCorrect the reported error and resume this phase.\n",
            encoding="utf-8",
        )

    @staticmethod
    def _is_approved(content: str) -> bool:
        matches = DECISION_PATTERN.findall(content)
        return bool(matches) and matches[-1].upper() == "APPROVED"

    @staticmethod
    def _advance(state: dict[str, Any], state_path: Path, next_action: str) -> None:
        state["next_action"] = next_action
        state["updated_at"] = utc_now()
        MemoryStore.write_json_atomic(state_path, state)

    @staticmethod
    def _mark_running_step(state: dict[str, Any], status: StepStatus, error: str) -> None:
        running = next(
            (item for item in reversed(state["steps"]) if item["status"] == StepStatus.RUNNING),
            None,
        )
        if running:
            running.update(status=status, finished_at=utc_now(), error=error)

    @staticmethod
    def _finish(
        state: dict[str, Any],
        state_path: Path,
        status: PhaseStatus,
        error: str | None = None,
    ) -> None:
        state.update(status=status, finished_at=utc_now(), error=error)
        MemoryStore.write_json_atomic(state_path, state)

    @staticmethod
    def _initial_state(phase_id: str, prompt: str) -> dict[str, Any]:
        return {
            "version": 3,
            "phase_id": phase_id,
            "prompt": prompt,
            "status": PhaseStatus.RUNNING,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "finished_at": None,
            "error": None,
            "next_action": "planning",
            "review_cycle": 1,
            "review_decision": None,
            "approved": False,
            "remaining_issues": None,
            "commit": None,
            "steps": [],
        }

    @staticmethod
    def _attach_phase_log(phase_dir: Path) -> tuple[logging.FileHandler, int]:
        handler = logging.FileHandler(phase_dir / "logs" / "workflow.log", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
        handler.setLevel(logging.INFO)
        previous_level = LOGGER.level
        LOGGER.setLevel(logging.DEBUG)
        LOGGER.addHandler(handler)
        return handler, previous_level

    @staticmethod
    def _close_phase_log(handler: logging.FileHandler, previous_level: int) -> None:
        LOGGER.removeHandler(handler)
        handler.close()
        LOGGER.setLevel(previous_level)
