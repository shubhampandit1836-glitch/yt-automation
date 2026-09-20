"""FastAPI entry point for the autonomous channel control plane."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import build_router
from .config import Settings, get_settings
from .db import Database
from .events import EventBus
from .services.governor import ResourceGovernor
from .services.providers import ProviderRouter


def create_app(database: Database | None = None, settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    db = database or Database(config.database_path)
    bus = EventBus(db)
    governor = ResourceGovernor(db)
    router = ProviderRouter(
        governor,
        dry_run=config.dry_run,
        gemini_api_key=config.gemini_api_key,
        gemini_model=config.gemini_model,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.ready = True
        yield
        application.state.ready = False

    application = FastAPI(
        title=config.app_name,
        version="0.1.0",
        description=(
            "Observable, safe-by-default control plane for a LangGraph-based "
            "Hinglish gaming channel. API and dashboard are never required "
            "for a queued production run."
        ),
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    application.include_router(build_router(db, bus, governor, config))

    @application.get("/", include_in_schema=False)
    def root() -> dict[str, Any]:
        return {
            "service": config.app_name,
            "docs": "/docs",
            "api": "/api/v1",
            "mode": "dry-run" if config.dry_run else "provider-backed",
        }

    return application


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("backend.app.main:app", host=settings.api_host, port=settings.api_port, reload=False)


if __name__ == "__main__":
    run()
