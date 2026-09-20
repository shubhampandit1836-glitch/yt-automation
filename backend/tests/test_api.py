from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.main import create_app
from backend.app.services.governor import ResourceGovernor
from backend.app.services.providers import ProviderRouter
from backend.app.events import EventBus
from backend.app.graphs.pipeline import PipelineRunner


def make_app() -> tuple[TestClient, Database]:
    database = Database(":memory:")
    settings = Settings(database_path=":memory:", dry_run=True)
    return TestClient(create_app(database, settings)), database


def test_health_and_safe_overview() -> None:
    client, _ = make_app()
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    overview = client.get("/api/v1/overview").json()
    assert overview["kill_switch"] is False
    assert overview["queue"]["queued"] == 0


def test_force_run_only_enqueues_and_controls_are_audited() -> None:
    client, database = make_app()
    response = client.post("/api/v1/controls/force-run", json={"topic": "A test brief"})
    assert response.status_code == 202
    assert client.get("/api/v1/jobs").json()[0]["status"] == "queued"

    pause = client.post("/api/v1/controls/pause", json={"reason": "test"})
    assert pause.status_code == 200
    assert database.setting("publishing_paused") == "true"
    assert database.list_audit(limit=1)[0]["action"] == "publishing.paused"


def test_pipeline_fails_closed_when_local_facts_are_unverified() -> None:
    database = Database(":memory:")
    job = database.create_job("production", {"topic": "No hallucinations", "format": "short"})
    events = EventBus(database)
    runner = PipelineRunner(
        database,
        events,
        ProviderRouter(ResourceGovernor(database), dry_run=True),
    )

    result = runner.run(job)
    video = database.get_video(result["video_id"])
    assert result["status"] == "blocked"
    assert video is not None
    assert video["status"] == "blocked"
    assert video["qa_report"]["publishable"] is False
    assert any(event["event_type"] == "run.completed" for event in database.list_events(result["run_id"]))
