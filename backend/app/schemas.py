"""Public API contracts.

These schemas are intentionally separate from the persistence representation so
OpenAPI remains a stable contract for the React dashboard and later clients.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    environment: str
    database: str
    dry_run: bool
    timestamp: datetime


class JobCreate(BaseModel):
    type: Literal["production", "research", "monitor", "comments", "ops", "learning"] = "production"
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=50, ge=1, le=100)
    run_after: datetime | None = None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    payload_json: str
    priority: int
    run_after: str
    attempts: int
    max_attempts: int
    status: str
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class RunResponse(BaseModel):
    id: str
    job_id: str | None = None
    graph: str
    status: str
    current_node: str | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class VideoResponse(BaseModel):
    id: str
    status: str
    format: str
    content_type: str
    topic: str
    title: str | None = None
    slot: str | None = None
    duration_seconds: float | None = None
    script: str | None = None
    fact_sheet: list[dict[str, Any]] = Field(default_factory=list)
    qa_report: dict[str, Any] = Field(default_factory=dict)
    license_ledger: list[dict[str, Any]] = Field(default_factory=list)
    degradation_level: str
    dry_run: bool
    created_at: str
    updated_at: str
    published_at: str | None = None
    job_id: str | None = None
    youtube_video_id: str | None = None
    youtube_url: str | None = None
    media_path: str | None = None
    metrics: list[dict[str, Any]] = Field(default_factory=list)


class MetricResponse(BaseModel):
    id: int
    video_id: str | None = None
    captured_at: str
    age_hours: float
    views: int
    watch_time_minutes: float
    average_view_percentage: float | None = None
    ctr: float | None = None
    likes: int
    comments: int
    source: str


class ControlRequest(BaseModel):
    actor: str = "dashboard-owner"
    reason: str = Field(default="dashboard control", min_length=1, max_length=300)


class ForceRunRequest(BaseModel):
    actor: str = "dashboard-owner"
    reason: str = Field(default="manual force run", min_length=1, max_length=300)
    topic: str | None = Field(default=None, max_length=180)
    format: Literal["short", "long"] = "short"
    content_type: Literal["news", "facts", "success_downfall", "comparison", "series", "trend"] = "facts"


class EventResponse(BaseModel):
    id: int
    run_id: str
    event_type: str
    node: str | None = None
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
