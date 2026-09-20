"""REST and SSE surface for the dashboard."""

from __future__ import annotations

import asyncio
import json
from html import escape
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse

from .config import Settings
from .db import Database, utc_now
from .events import EventBus
from .schemas import (
    ControlRequest,
    EventResponse,
    ForceRunRequest,
    HealthResponse,
    JobCreate,
    JobResponse,
    RunResponse,
    VideoResponse,
)
from .services.assets import AssetService
from .services.governor import ResourceGovernor
from .services.memory import MemoryService
from .services.youtube import YouTubeNotConfigured, YouTubeService


def build_router(database: Database, events: EventBus, governor: ResourceGovernor, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    youtube = YouTubeService(database, settings)
    memory = MemoryService(database, settings.gemini_api_key, settings.gemini_embedding_model)
    assets = AssetService(settings.asset_dir, settings.pexels_api_key, settings.pixabay_api_key)

    def owner_guard(x_owner_email: str | None = Header(default=None)) -> str:
        # Production deployments must put Google/Cloudflare Access in front of
        # this API. The header is a deliberately small local seam for that
        # integration; development remains convenient without auth ceremony.
        if settings.is_production and x_owner_email != settings.owner_email:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="owner authentication required")
        return x_owner_email or "local-owner"

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        paused = database.setting("publishing_paused", "false") == "true"
        return HealthResponse(
            status="degraded" if paused else "ok",
            service=settings.app_name,
            environment=settings.app_env,
            database="connected",
            dry_run=settings.dry_run,
            timestamp=datetime.now(UTC),
        )

    @router.get("/auth/youtube/status")
    def youtube_auth_status() -> dict[str, Any]:
        return youtube.status()

    @router.get("/auth/youtube/start")
    def youtube_auth_start(_: str = Depends(owner_guard)) -> dict[str, Any]:
        try:
            return {"authorization_url": youtube.authorization_url()}
        except YouTubeNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/auth/youtube/callback", response_class=HTMLResponse)
    def youtube_auth_callback(code: str, state: str | None = None) -> HTMLResponse:
        try:
            channel = youtube.complete_authorization(code, state)
        except YouTubeNotConfigured as exc:
            return HTMLResponse(f"<h1>YouTube connection failed</h1><p>{exc}</p>", status_code=400)
        title = escape(channel.get("snippet", {}).get("title", "channel"))
        return HTMLResponse(
            f"<h1>YouTube connected</h1><p>{title} is connected. You can close this tab.</p>"
        )

    @router.get("/channel")
    def channel() -> dict[str, Any]:
        try:
            return youtube.get_channel()
        except YouTubeNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/channel/sync")
    def channel_sync(_: str = Depends(owner_guard)) -> dict[str, Any]:
        try:
            return youtube.sync_channel()
        except YouTubeNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/channel/analytics")
    def channel_analytics(_: str = Depends(owner_guard)) -> dict[str, Any]:
        try:
            return youtube.analytics_snapshot()
        except YouTubeNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/overview")
    def overview() -> dict[str, Any]:
        youtube_status = youtube.status()
        data = database.overview()
        ready = bool(not settings.dry_run and youtube_status.get("connected"))
        data["automation"] = {
            "mode": "autopilot" if ready else ("setup" if not settings.dry_run else "preview"),
            "ready": ready,
            "message": (
                "The worker and scheduler can run without the dashboard."
                if ready
                else "Preview only: connect verified research, rendering and YouTube upload adapters before publishing."
            ),
            "setup_needed": [
                "verified research provider",
                "render and TTS provider",
                "YouTube OAuth and quota audit",
            ] if not ready else [],
        }
        data["governor"] = governor.snapshot()
        return data

    @router.get("/jobs", response_model=list[JobResponse])
    def jobs(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
        return database.list_jobs(limit)

    @router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
    def create_job(payload: JobCreate, _: str = Depends(owner_guard)) -> dict[str, Any]:
        if database.setting("kill_switch", "false") == "true" and payload.type == "production":
            raise HTTPException(status_code=409, detail="kill switch is active")
        return database.create_job(
            payload.type,
            payload.payload,
            priority=payload.priority,
            run_after=payload.run_after.isoformat() if payload.run_after else None,
        )

    @router.get("/runs", response_model=list[RunResponse])
    def runs(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
        return database.list_runs(limit)

    @router.get("/runs/{run_id}", response_model=RunResponse)
    def run(run_id: str) -> dict[str, Any]:
        value = database.get_run(run_id)
        if not value:
            raise HTTPException(status_code=404, detail="run not found")
        return value

    @router.get("/runs/{run_id}/events", response_model=list[EventResponse])
    def run_events(run_id: str, limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        if not database.get_run(run_id):
            raise HTTPException(status_code=404, detail="run not found")
        return database.list_events(run_id, limit)

    @router.get("/events", response_model=list[EventResponse])
    def events_list(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return database.list_events(limit=limit)

    @router.get("/events/stream")
    async def events_stream(request: Request) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            # A comment keeps proxies from deciding the connection is idle.
            yield ": connected\n\n"
            async for event in events.subscribe():
                if await request.is_disconnected():
                    break
                # Keep the stream on the default SSE event channel. The event type is
                # still present in the JSON payload, which lets a browser use
                # EventSource.onmessage without registering every node name.
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    @router.get("/videos", response_model=list[VideoResponse])
    def videos(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
        return database.list_videos(limit)

    @router.get("/videos/{video_id}", response_model=VideoResponse)
    def video(video_id: str) -> dict[str, Any]:
        value = database.get_video(video_id)
        if not value:
            raise HTTPException(status_code=404, detail="video not found")
        value["metrics"] = database.list_metrics(video_id)
        return value

    @router.get("/metrics")
    def metrics(video_id: str | None = None, limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return database.list_metrics(video_id, limit)

    @router.get("/series")
    def series() -> list[dict[str, Any]]:
        return database.list_series()

    @router.get("/experiments")
    def experiments() -> list[dict[str, Any]]:
        return database.list_experiments()

    @router.get("/comments")
    def comments(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        items = database.list_comments(limit)
        return {
            "items": items,
            "insights": {
                "requests": sum(item["label"] == "request" for item in items),
                "corrections": sum(item["label"] == "factual_correction" for item in items),
                "spam": sum(item["label"] == "spam" for item in items),
                "replies": sum(bool(item.get("replied_at")) for item in items),
            },
            "message": "Comments are classified by the worker; uncertain comments stay out of memory.",
        }

    @router.get("/memory/search")
    def memory_search(q: str = Query(default="", max_length=200)) -> dict[str, Any]:
        memories = memory.search(q, 20) if q else memory.search("", 20)
        if not memories:
            memories = [
                {"kind": "style", "title": "Hinglish style guide", "content": "Short, friendly, sourced, no creator imitation.", "score": 0},
                {"kind": "policy", "title": "Safe default", "content": "Unverified claims are blocked rather than softened.", "score": 0},
            ]
        return {"query": q, "items": memories, "backend": "gemini-embedding-with-local-cosine-fallback"}

    @router.get("/playbook")
    def playbook() -> dict[str, Any]:
        with database.connection() as connection:
            rows = connection.execute("SELECT * FROM playbook_rules ORDER BY updated_at DESC").fetchall()
        return {"rules": [dict(row) for row in rows]}

    @router.get("/assets/search")
    def assets_search(q: str = Query(..., min_length=2, max_length=120)) -> dict[str, Any]:
        return {"query": q, "items": assets.search(q), "licensed": True}

    @router.get("/ops")
    def ops() -> dict[str, Any]:
        return {
            "queue": database.overview()["queue"],
            "providers": governor.snapshot(),
            "events": {"subscribers": events.subscriber_count, "latest": database.list_events(limit=8)},
            "audit": database.list_audit(limit=20),
            "kill_switch": database.setting("kill_switch", "false") == "true",
            "publishing_paused": database.setting("publishing_paused", "false") == "true",
        }

    def set_control(key: str, value: str, action: str, request: ControlRequest, actor: str) -> dict[str, Any]:
        database.set_setting(key, value, actor)
        database.record_audit(actor, action, key, {"reason": request.reason, "value": value})
        return {"ok": True, "setting": key, "value": value, "reason": request.reason}

    @router.post("/controls/pause")
    def pause(request: ControlRequest, actor: str = Depends(owner_guard)) -> dict[str, Any]:
        return set_control("publishing_paused", "true", "publishing.paused", request, actor)

    @router.post("/controls/resume")
    def resume(request: ControlRequest, actor: str = Depends(owner_guard)) -> dict[str, Any]:
        return set_control("publishing_paused", "false", "publishing.resumed", request, actor)

    @router.post("/controls/kill-switch")
    def kill_switch(request: ControlRequest, actor: str = Depends(owner_guard)) -> dict[str, Any]:
        return set_control("kill_switch", "true", "kill_switch.enabled", request, actor)

    @router.post("/controls/clear-kill-switch")
    def clear_kill_switch(request: ControlRequest, actor: str = Depends(owner_guard)) -> dict[str, Any]:
        return set_control("kill_switch", "false", "kill_switch.disabled", request, actor)

    @router.post("/controls/force-run", status_code=status.HTTP_202_ACCEPTED)
    def force_run(request: ForceRunRequest, actor: str = Depends(owner_guard)) -> dict[str, Any]:
        if database.setting("kill_switch", "false") == "true":
            raise HTTPException(status_code=409, detail="kill switch is active")
        job = database.create_job(
            "production",
            {
                "topic": request.topic,
                "format": request.format,
                "content_type": request.content_type,
                "forced": True,
            },
            priority=10,
        )
        database.record_audit(actor, "job.force_run", job["id"], {"reason": request.reason})
        return {"ok": True, "job": job, "message": "Queued for a worker; the API stays off the critical path."}

    return router
