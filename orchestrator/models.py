"""Typed data exchanged between the workflow and agent adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Mapping


class PhaseStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Immutable context supplied to one agent execution."""

    phase_id: str
    phase_prompt: str
    step_id: str
    objective: str
    project_root: Path
    workspace_dir: Path
    phase_dir: Path
    memory: Mapping[str, str]
    prior_results: tuple["AgentResult", ...] = ()


@dataclass(frozen=True, slots=True)
class AgentResult:
    """A persistable handoff produced by an agent."""

    agent_name: str
    step_id: str
    summary: str
    content: str
    metadata: Mapping[str, str] = field(default_factory=dict)
