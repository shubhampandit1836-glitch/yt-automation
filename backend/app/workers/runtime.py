"""Postgres/SQLite queue worker entry point.

Workers claim rows with a lock in the repository, then execute a resumable
pipeline graph. The API process never performs this work.
"""

from __future__ import annotations

import argparse
import time

from ..config import get_settings
from ..db import Database
from ..events import EventBus
from ..graphs.pipeline import PipelineRunner
from ..services.alerts import AlertService
from ..services.assets import AssetService, ThumbnailService
from ..services.comments import CommentService
from ..services.governor import ResourceGovernor
from ..services.learning import LearningService
from ..services.media import RenderService
from ..services.memory import MemoryService
from ..services.providers import ProviderRouter
from ..services.research import ResearchService
from ..services.youtube import YouTubeService


class Worker:
    def __init__(self, database: Database | None = None) -> None:
        settings = get_settings()
        self.settings = settings
        self.database = database or Database(settings.database_path)
        self.events = EventBus(self.database)
        self.governor = ResourceGovernor(self.database)
        self.router = ProviderRouter(
            self.governor,
            dry_run=settings.dry_run,
            gemini_api_key=settings.gemini_api_key,
            gemini_model=settings.gemini_model,
        )
        self.youtube = YouTubeService(self.database, settings)
        self.researcher = ResearchService(self.router, settings.rss_url_list, self.youtube)
        self.renderer = RenderService(settings.media_dir, settings.tts_voice, settings.groq_api_key)
        self.assets = AssetService(settings.asset_dir, settings.pexels_api_key, settings.pixabay_api_key)
        self.thumbnails = ThumbnailService(settings.asset_dir)
        self.memory = MemoryService(self.database, settings.gemini_api_key, settings.gemini_embedding_model)
        self.comments = CommentService(
            self.database,
            self.youtube,
            self.router,
            replies_enabled=settings.comment_replies_enabled,
            max_replies=settings.comment_max_replies,
        )
        self.learning = LearningService(self.database)
        self.alerts = AlertService(settings.telegram_bot_token, settings.telegram_chat_id, settings.heartbeat_url)
        self.pipeline = PipelineRunner(
            self.database,
            self.events,
            self.router,
            researcher=self.researcher,
            renderer=self.renderer,
            youtube=self.youtube,
            thumbnails=self.thumbnails,
            assets=self.assets,
            memory=self.memory,
        )

    def run_auxiliary(self, job: dict) -> None:
        """Run a cheap control-plane job without entering the media graph.

        The scheduler creates monitor, comments, research, learning and ops
        jobs at different cadences. Keeping these as explicit run records now
        prevents a future adapter from accidentally treating a watchdog tick
        as a video production request.
        """
        from ..db import json_loads

        payload = json_loads(job.get("payload_json"), {})
        state = {"job_id": job["id"], "kind": payload.get("kind", job["type"]), "mode": "local"}
        run = self.database.create_run(job["type"], job["id"], state)
        self.database.update_run(run["id"], status="running", node=payload.get("kind", job["type"]), state=state)
        self.events.publish(run["id"], "run.started", f"{job['type'].title()} cycle started", payload=payload)
        if job["type"] == "monitor" and payload.get("kind") == "youtube-sync":
            state["result"] = "dry-run: YouTube sync skipped" if self.settings.dry_run else self.youtube.sync_channel()
        elif job["type"] == "monitor" and payload.get("kind") == "analytics-deep":
            state["result"] = "dry-run: Analytics sync skipped" if self.settings.dry_run else self.youtube.analytics_snapshot()
        elif job["type"] == "comments":
            state["result"] = {"comments_fetched": 0, "replies_posted": 0} if self.settings.dry_run else self.comments.run_cycle([video["id"] for video in self.database.list_videos(100)])
        elif job["type"] == "learning":
            state["result"] = self.learning.cycle()
        elif job["type"] == "ops":
            state["result"] = {"heartbeat": self.alerts.heartbeat(success=True), "provider_health": self.database.provider_health()}
        else:
            # Research, monitoring and comment adapters remain independently
            # retryable jobs; never mark a fake external read as successful.
            state["result"] = "adapter not configured; no external request made"
        self.database.update_run(run["id"], status="succeeded", node=payload.get("kind", job["type"]), state=state)
        self.events.publish(run["id"], "run.completed", f"{job['type'].title()} cycle completed", payload=state)

    def run_once(self) -> bool:
        job = self.database.claim_job()
        if not job:
            return False
        if self.database.setting("kill_switch", "false") == "true" and job["type"] == "production":
            self.database.finish_job(job["id"], "cancelled", "kill switch is active")
            return True
        try:
            if job["type"] == "production":
                result = self.pipeline.run(job)
                if result.get("status") == "scheduled":
                    self.alerts.publish_success(result.get("title", "video"), result.get("youtube_url"))
            else:
                self.run_auxiliary(job)
            self.database.finish_job(job["id"], "succeeded")
        except Exception as exc:  # noqa: BLE001 - job boundary must retain the error
            try:
                self.alerts.failure(str(exc))
            except Exception:
                pass
            if job["attempts"] < job["max_attempts"]:
                self.database.retry_job(job["id"], str(exc), delay_seconds=2 ** job["attempts"])
            else:
                self.database.finish_job(job["id"], "failed", str(exc))
        return True

    def serve(self, poll_seconds: float = 2.0) -> None:
        while True:
            worked = self.run_once()
            if not worked:
                time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the autonomous channel worker")
    parser.add_argument("--once", action="store_true", help="claim at most one job and exit")
    args = parser.parse_args()
    worker = Worker()
    if args.once:
        worker.run_once()
    else:
        worker.serve()


if __name__ == "__main__":
    main()
