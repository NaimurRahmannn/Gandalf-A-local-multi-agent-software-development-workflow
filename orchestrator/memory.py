"""Persistence for shared memory, phase state, task handoffs, and logs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from orchestrator.models import AgentResult


MEMORY_FILES = (
    "project.md",
    "architecture.md",
    "decisions.md",
    "progress.md",
    "team-rules.md",
)

RESULT_FILENAMES = {
    "planning": "antigravity-plan.md",
    "implementation": "codex-report.md",
    "review": "cursor-review.md",
    "final-review": "antigravity-final-review.md",
    "improvement": "codex-improvement-report.md",
}


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
        return phase_id, phase_dir

    @staticmethod
    def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def write_result(phase_dir: Path, order: int, result: AgentResult) -> Path:
        task_path = phase_dir / "tasks" / f"{order:02d}-{result.step_id}.md"
        artifact_path = phase_dir / RESULT_FILENAMES.get(
            result.step_id, f"{result.agent_name}-{result.step_id}.md"
        )
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
        artifact_path.write_text(body, encoding="utf-8")
        return artifact_path

    def append_progress(self, phase_id: str, status: str, detail: str) -> None:
        path = self.memory_dir / "progress.md"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"\n- {utc_now()} | `{phase_id}` | **{status}** | {detail}\n")
