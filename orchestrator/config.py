"""Configuration loading, path resolution, and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from orchestrator.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class PathsConfig:
    memory_dir: Path
    workspace_dir: Path
    phases_dir: Path


@dataclass(frozen=True, slots=True)
class AgentConfig:
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class AppConfig:
    project_root: Path
    paths: PathsConfig
    agents: dict[str, AgentConfig]
    log_level: str = "INFO"


REQUIRED_AGENTS = ("antigravity", "codex", "cursor")


def _mapping(value: Any, key: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(f"Configuration key '{key}' must be a mapping.")
    return value


def _resolve(root: Path, value: Any, key: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"Configuration key '{key}' must be a path string.")
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def load_config(config_path: Path, project_root: Path) -> AppConfig:
    """Load YAML and resolve all project paths to absolute paths."""

    if not config_path.is_file():
        raise ConfigurationError(f"Configuration file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Unable to read configuration: {exc}") from exc
    root = project_root.resolve()
    data = _mapping(raw, "root")
    paths_data = _mapping(data.get("paths"), "paths")
    memory_dir = _resolve(root, paths_data.get("memory_dir", ".ai-memory"), "paths.memory_dir")
    workspace_dir = _resolve(root, paths_data.get("workspace_dir", "workspace"), "paths.workspace_dir")
    phases_value = paths_data.get("phases_dir", "phases")
    phases_candidate = Path(phases_value) if isinstance(phases_value, str) else None
    if phases_candidate is None or not phases_value.strip():
        raise ConfigurationError("Configuration key 'paths.phases_dir' must be a path string.")
    phases_dir = (
        phases_candidate.resolve()
        if phases_candidate.is_absolute()
        else (memory_dir / phases_candidate).resolve()
    )

    agents_data = _mapping(data.get("agents"), "agents")
    agents: dict[str, AgentConfig] = {}
    for name in REQUIRED_AGENTS:
        agent_data = _mapping(agents_data.get(name), f"agents.{name}")
        enabled = agent_data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigurationError(f"agents.{name}.enabled must be true or false.")
        agents[name] = AgentConfig(enabled=enabled)

    logging_data = _mapping(data.get("logging"), "logging")
    level = str(logging_data.get("level", "INFO")).upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigurationError(f"Unsupported logging level: {level}")
    return AppConfig(root, PathsConfig(memory_dir, workspace_dir, phases_dir), agents, level)
