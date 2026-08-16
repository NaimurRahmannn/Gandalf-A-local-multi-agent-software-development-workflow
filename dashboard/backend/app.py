"""FastAPI application factory and local dashboard endpoints."""

import asyncio
import json
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from dashboard.backend.database import DashboardDatabase
from dashboard.backend.jobs import DashboardJobService, DashboardServiceError
from dashboard.backend.schemas import ApprovalInput, PhaseStart, ProjectCreate
from dashboard.backend.settings import DashboardSettings
from orchestrator.config import load_config
from orchestrator.core.cli_runner import CliRunner
from orchestrator.models import ApprovalStatus


ARTIFACT_NAMES = (
    "prompt.md",
    "plan.md",
    "implementation.md",
    "changes.diff",
    "review.md",
    "improvements.md",
    "test-results.md",
    "phase-report.md",
    "before-state.txt",
    "after-state.txt",
    "phase-status.json",
    "status.json",
)
MAX_TEXT_BYTES = 2_000_000


def create_app(
    settings: DashboardSettings,
    database: DashboardDatabase | None = None,
    jobs: DashboardJobService | None = None,
) -> FastAPI:
    db = database or DashboardDatabase(settings.database_path)
    job_service = jobs or DashboardJobService(settings, db)
    security = HTTPBasic(auto_error=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        db.initialize()
        job_service.recover()
        yield
        job_service.shutdown()

    app = FastAPI(
        title="Local AI Team Dashboard",
        version="0.4.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = db
    app.state.jobs = job_service

    def authenticate(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
    ) -> str:
        if credentials is None:
            raise _unauthorized()
        username_ok = secrets.compare_digest(
            credentials.username.encode("utf-8"), settings.username.encode("utf-8")
        )
        password_ok = secrets.compare_digest(
            credentials.password.encode("utf-8"), settings.password.encode("utf-8")
        )
        if not username_ok or not password_ok:
            raise _unauthorized()
        return credentials.username

    auth = Annotated[str, Depends(authenticate)]

    @app.get("/", include_in_schema=False)
    def dashboard_home(_username: auth) -> FileResponse:
        return FileResponse(settings.frontend_dir / "index.html")

    @app.get("/app.js", include_in_schema=False)
    def dashboard_javascript(_username: auth) -> FileResponse:
        return FileResponse(settings.frontend_dir / "app.js", media_type="text/javascript")

    @app.get("/styles.css", include_in_schema=False)
    def dashboard_styles(_username: auth) -> FileResponse:
        return FileResponse(settings.frontend_dir / "styles.css", media_type="text/css")

    @app.get("/projects")
    def list_projects(_username: auth) -> list[dict[str, Any]]:
        return db.list_projects()

    @app.post("/projects", status_code=status.HTTP_201_CREATED)
    def create_project(payload: ProjectCreate, _username: auth) -> dict[str, Any]:
        try:
            return job_service.create_project(payload.name)
        except DashboardServiceError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.get("/projects/{project_id}")
    def get_project(project_id: str, _username: auth) -> dict[str, Any]:
        project = db.get_project(project_id)
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        memory_path = Path(project["memory_path"])
        project["memory"] = {
            name: _read_text(memory_path / name)
            for name in ("project.md", "architecture.md", "decisions.md", "progress.md", "team-rules.md")
        }
        project["phases"] = db.list_phases(project_id)
        return project

    @app.get("/phases")
    def list_phases(
        _username: auth, project_id: str | None = Query(default=None)
    ) -> list[dict[str, Any]]:
        return db.list_phases(project_id)

    @app.post("/phases/start", status_code=status.HTTP_202_ACCEPTED)
    def start_phase(payload: PhaseStart, _username: auth) -> dict[str, Any]:
        try:
            return job_service.start_phase(payload.project_id, payload.prompt)
        except DashboardServiceError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.get("/phases/{phase_id}")
    def get_phase(phase_id: str, _username: auth) -> dict[str, Any]:
        return _phase_detail(db, phase_id)

    @app.post("/phases/{phase_id}/resume", status_code=status.HTTP_202_ACCEPTED)
    def resume_phase(phase_id: str, _username: auth) -> dict[str, Any]:
        try:
            return job_service.resume_phase(phase_id)
        except DashboardServiceError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.post("/phases/{phase_id}/approve")
    def approve_phase(
        phase_id: str, payload: ApprovalInput, _username: auth
    ) -> dict[str, Any]:
        return _resolve(job_service, phase_id, ApprovalStatus.APPROVED, payload.feedback)

    @app.post("/phases/{phase_id}/reject")
    def reject_phase(
        phase_id: str, payload: ApprovalInput, _username: auth
    ) -> dict[str, Any]:
        return _resolve(job_service, phase_id, ApprovalStatus.REJECTED, payload.feedback)

    @app.post("/phases/{phase_id}/request-changes")
    def request_changes(
        phase_id: str, payload: ApprovalInput, _username: auth
    ) -> dict[str, Any]:
        if not payload.feedback.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Feedback is required")
        return _resolve(
            job_service, phase_id, ApprovalStatus.CHANGES_REQUESTED, payload.feedback
        )

    @app.get("/agents/status")
    def agent_status(_username: auth) -> list[dict[str, Any]]:
        config = load_config(settings.orchestrator_config, settings.repository_root)
        runner = CliRunner()
        statuses = []
        for name, agent in config.agents.items():
            executable = runner.split_command(agent.command)[0]
            statuses.append(
                {
                    "name": name,
                    "enabled": agent.enabled,
                    "command": agent.command,
                    "installed": shutil.which(executable) is not None,
                }
            )
        return statuses

    @app.get("/logs/{phase_id}")
    def get_logs(phase_id: str, _username: auth) -> dict[str, str]:
        phase = db.get_phase(phase_id)
        if phase is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Phase not found")
        if not phase.get("phase_dir"):
            return {}
        log_dir = Path(phase["phase_dir"]) / "logs"
        if not log_dir.is_dir():
            return {}
        return {
            path.name: _read_text(path)
            for path in sorted(log_dir.iterdir())
            if path.is_file()
        }

    @app.get("/events/{phase_id}")
    async def stream_events(
        phase_id: str,
        request: Request,
        _username: auth,
        after_id: int = Query(default=0, ge=0),
        once: bool = Query(default=False),
    ) -> StreamingResponse:
        if db.get_phase(phase_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Phase not found")

        async def generate() -> AsyncIterator[str]:
            cursor = after_id
            while True:
                events = db.list_events(phase_id, cursor)
                for event in events:
                    cursor = int(event["id"])
                    yield f"id: {cursor}\nevent: phase\ndata: {json.dumps(event)}\n\n"
                if once:
                    break
                if await request.is_disconnected():
                    break
                if not events:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/notifications")
    def list_notifications(
        _username: auth, unread_only: bool = Query(default=False)
    ) -> list[dict[str, Any]]:
        return db.list_notifications(unread_only)

    @app.post("/notifications/{notification_id}/read")
    def read_notification(notification_id: str, _username: auth) -> dict[str, bool]:
        if not db.mark_notification_read(notification_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
        return {"updated": True}

    return app


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Authentication required",
        headers={"WWW-Authenticate": 'Basic realm="AI Team Dashboard"'},
    )


def _resolve(
    jobs: DashboardJobService,
    phase_id: str,
    resolution: ApprovalStatus,
    feedback: str,
) -> dict[str, Any]:
    try:
        return jobs.resolve_approval(phase_id, resolution, feedback)
    except DashboardServiceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    data = path.read_bytes()[:MAX_TEXT_BYTES]
    text = data.decode("utf-8", errors="replace")
    if path.stat().st_size > MAX_TEXT_BYTES:
        text += "\n\n[Output truncated by dashboard]"
    return text


def _phase_detail(database: DashboardDatabase, phase_id: str) -> dict[str, Any]:
    phase = database.get_phase(phase_id)
    if phase is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Phase not found")
    phase["events"] = database.list_events(phase_id)
    phase["approvals"] = database.list_approvals(phase_id)
    artifacts: dict[str, str] = {}
    if phase.get("phase_dir"):
        phase_dir = Path(phase["phase_dir"])
        artifacts = {name: _read_text(phase_dir / name) for name in ARTIFACT_NAMES}
    phase["artifacts"] = artifacts
    return phase
