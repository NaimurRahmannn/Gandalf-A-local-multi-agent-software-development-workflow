"""Persistence for shared memory, phase state, task handoffs, and logs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from orchestrator.models import AgentResult, DashboardStatus


MEMORY_FILES = (
    "project.md",
    "architecture.md",
    "decisions.md",
    "progress.md",
    "team-rules.md",
)

PHASE_DOCUMENTS = (
    "plan.md",
    "before-state.txt",
    "changes.diff",
    "implementation.md",
    "review.md",
    "improvements.md",
    "after-state.txt",
    "test-results.md",
    "phase-report.md",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Owns all filesystem operations for orchestrator state."""

    def __init__(self, memory_dir: Path, phases_dir: Path) -> None:
        self.memory_dir = memory_dir
        self.phases_dir = phases_dir

    def ensure_structure(self, workspace_dir: Path) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.phases_dir.mkdir(parents=True, exist_ok=True)
        (self.memory_dir / "reviews").mkdir(parents=True, exist_ok=True)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        for filename in MEMORY_FILES:
            path = self.memory_dir / filename
            if not path.exists():
                path.write_text(f"# {Path(filename).stem.replace('-', ' ').title()}\n", encoding="utf-8")

    def load_context(self) -> dict[str, str]:
        return {
            filename: (self.memory_dir / filename).read_text(encoding="utf-8")
            for filename in MEMORY_FILES
        }

    def create_phase(self, prompt: str) -> tuple[str, Path]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "phase"
        phase_id = f"{timestamp}-{slug}-{uuid4().hex[:8]}"
        phase_dir = self.phases_dir / phase_id
        (phase_dir / "tasks").mkdir(parents=True)
        (phase_dir / "logs").mkdir()
        (phase_dir / "prompt.md").write_text(f"# Phase Prompt\n\n{prompt}\n", encoding="utf-8")
        for filename in PHASE_DOCUMENTS:
            (phase_dir / filename).touch()
        self.write_json_atomic(phase_dir / "handoffs.json", {"results": []})
        self.write_phase_status(
            phase_dir,
            phase_id,
            prompt,
            DashboardStatus.CREATED,
            None,
            "Phase created",
        )
        return phase_id, phase_dir

    @staticmethod
    def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def write_phase_status(
        phase_dir: Path,
        phase_id: str,
        prompt: str,
        status: DashboardStatus,
        current_agent: str | None,
        message: str,
    ) -> None:
        MemoryStore.write_json_atomic(
            phase_dir / "phase-status.json",
            {
                "phase_id": phase_id,
                "phase": prompt,
                "status": status,
                "current_agent": current_agent,
                "message": message,
                "updated_at": utc_now(),
            },
        )

    @staticmethod
    def write_result(phase_dir: Path, order: int, result: AgentResult) -> Path:
        task_path = phase_dir / "tasks" / f"{order:02d}-{result.step_id}.md"
        artifact_name, append = MemoryStore._result_artifact(result.step_id, result.agent_name)
        artifact_path = phase_dir / artifact_name
        metadata = "\n".join(f"- {key}: {value}" for key, value in result.metadata.items())
        metadata_section = f"\n## Metadata\n\n{metadata}\n" if metadata else ""
        body = (
            f"# {result.summary}\n\n"
            f"- Agent: {result.agent_name}\n"
            f"- Step: {result.step_id}\n"
            f"- Generated: {utc_now()}\n"
            f"{metadata_section}\n## Handoff\n\n{result.content.rstrip()}\n"
        )
        task_path.write_text(body, encoding="utf-8")
        if append and artifact_path.stat().st_size:
            with artifact_path.open("a", encoding="utf-8") as stream:
                stream.write(f"\n---\n\n{body}")
        else:
            artifact_path.write_text(body, encoding="utf-8")

        # Keep Phase 2 stable output names as compatibility aliases.
        alias = MemoryStore._legacy_alias(result.step_id)
        if alias:
            (phase_dir / alias).write_text(body, encoding="utf-8")
        return artifact_path

    @staticmethod
    def _result_artifact(step_id: str, agent_name: str) -> tuple[str, bool]:
        if step_id == "planning":
            return "plan.md", False
        if step_id == "implementation":
            return "implementation.md", False
        if step_id.startswith("review-cycle-") or step_id.startswith("architecture-review-cycle-"):
            return "review.md", True
        if step_id.startswith("improvement-cycle-"):
            return "improvements.md", True
        return f"{agent_name}-{step_id}.md", False

    @staticmethod
    def _legacy_alias(step_id: str) -> str | None:
        if step_id == "planning":
            return "antigravity-plan.md"
        if step_id == "implementation":
            return "codex-report.md"
        if step_id.startswith("review-cycle-"):
            return "cursor-review.md"
        if step_id.startswith("architecture-review-cycle-"):
            return "antigravity-final-review.md"
        if step_id.startswith("improvement-cycle-"):
            return "codex-improvement-report.md"
        return None

    def get_phase(self, phase_id: str) -> Path:
        phase_dir = (self.phases_dir / phase_id).resolve()
        try:
            phase_dir.relative_to(self.phases_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"Invalid phase identifier: {phase_id}") from exc
        if not phase_dir.is_dir():
            raise FileNotFoundError(f"Phase not found: {phase_id}")
        return phase_dir

    @staticmethod
    def write_handoffs(phase_dir: Path, results: list[AgentResult]) -> None:
        payload = {
            "results": [
                {
                    "agent_name": result.agent_name,
                    "step_id": result.step_id,
                    "summary": result.summary,
                    "content": result.content,
                    "metadata": dict(result.metadata),
                }
                for result in results
            ]
        }
        MemoryStore.write_json_atomic(phase_dir / "handoffs.json", payload)

    @staticmethod
    def load_handoffs(phase_dir: Path) -> list[AgentResult]:
        path = phase_dir / "handoffs.json"
        if not path.is_file():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [AgentResult(**item) for item in payload.get("results", [])]

    def append_progress(self, phase_id: str, status: str, detail: str) -> None:
        path = self.memory_dir / "progress.md"
        safe_detail = " ".join(detail.split()) or "No details provided"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"\n- {utc_now()} | `{phase_id}` | **{status}** | {safe_detail}\n")

    def update_project_memory(
        self,
        phase_id: str,
        status: str,
        changed_files: tuple[str, ...],
        decision: str,
    ) -> None:
        files = ", ".join(f"`{name}`" for name in changed_files) or "None"
        decisions_path = self.memory_dir / "decisions.md"
        decisions_marker = f"## Phase {phase_id}"
        if decisions_marker not in decisions_path.read_text(encoding="utf-8"):
            with decisions_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    f"\n{decisions_marker}\n\n"
                    f"- Outcome: {status}\n"
                    f"- Review decision: {decision}\n"
                    f"- Detailed record: `phases/{phase_id}/phase-report.md`\n"
                )
        architecture_path = self.memory_dir / "architecture.md"
        architecture_marker = f"## Phase {phase_id} change record"
        if architecture_marker not in architecture_path.read_text(encoding="utf-8"):
            with architecture_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    f"\n{architecture_marker}\n\n"
                    f"- Outcome: {status}\n"
                    f"- Files changed: {files}\n"
                    "- Architecture details and recommendations are recorded in the phase report.\n"
                )
