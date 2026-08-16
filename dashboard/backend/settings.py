"""Dashboard configuration loaded from YAML and environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class DashboardConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DashboardSettings:
    repository_root: Path
    database_path: Path
    projects_root: Path
    orchestrator_config: Path
    frontend_dir: Path
    password: str
    session_secret: str
    username: str = "admin"
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 2


def _resolve(root: Path, value: Any, key: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DashboardConfigurationError(f"Dashboard setting '{key}' must be a path string.")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def load_dashboard_settings(config_path: Path | None = None) -> DashboardSettings:
    repository_root = Path(__file__).resolve().parents[2]
    path = (config_path or repository_root / "dashboard" / "config.yaml").resolve()
    if not path.is_file():
        raise DashboardConfigurationError(f"Dashboard configuration not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise DashboardConfigurationError(f"Unable to read dashboard configuration: {exc}") from exc
    if not isinstance(data, dict):
        raise DashboardConfigurationError("Dashboard configuration root must be a mapping.")
    server = data.get("server") or {}
    security = data.get("security") or {}
    paths = data.get("paths") or {}
    if not all(isinstance(item, dict) for item in (server, security, paths)):
        raise DashboardConfigurationError("server, security, and paths must be mappings.")

    password_env = str(security.get("password_env", "AI_TEAM_DASHBOARD_PASSWORD"))
    secret_env = str(security.get("session_secret_env", "AI_TEAM_SESSION_SECRET"))
    password = os.environ.get(password_env, "")
    if not password:
        raise DashboardConfigurationError(
            f"Set the {password_env} environment variable before starting the dashboard."
        )
    secret = os.environ.get(secret_env) or password
    port = server.get("port", 8000)
    workers = server.get("workers", 2)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise DashboardConfigurationError("server.port must be an integer from 1 to 65535.")
    if not isinstance(workers, int) or workers <= 0:
        raise DashboardConfigurationError("server.workers must be a positive integer.")
    return DashboardSettings(
        repository_root=repository_root,
        database_path=_resolve(repository_root, paths.get("database", "dashboard/data/dashboard.db"), "paths.database"),
        projects_root=_resolve(repository_root, paths.get("projects_root", "workspace"), "paths.projects_root"),
        orchestrator_config=_resolve(repository_root, paths.get("orchestrator_config", "orchestrator/config.yaml"), "paths.orchestrator_config"),
        frontend_dir=_resolve(repository_root, paths.get("frontend", "dashboard/frontend"), "paths.frontend"),
        password=password,
        session_secret=secret,
        username=str(security.get("username", "admin")),
        host=str(server.get("host", "127.0.0.1")),
        port=port,
        workers=workers,
    )
