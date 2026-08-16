"""Uvicorn import target for the local dashboard."""

from dashboard.backend.app import create_app
from dashboard.backend.settings import load_dashboard_settings

settings = load_dashboard_settings()
app = create_app(settings)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
