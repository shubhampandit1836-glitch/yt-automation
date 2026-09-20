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
from ..services.governor import ResourceGovernor
from ..services.providers import ProviderRouter


class Worker:
    def __init__(self, database: Database | None = None) -> None:
        settings = get_settings()
        self.settings = settings
        self.database = database or Database(settings.database_path)
        self.events = EventBus(self.database)
        self.governor = ResourceGovernor(self.database)
        self.router = ProviderRouter(self.governor, dry_run=settings.dry_run)
        self.pipeline = PipelineRunner(self.database, self.events, self.router)

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
        # Provider-backed implementations attach to this seam. In safe local
        # mode, a completed no-op is more honest than pretending to have read
        # YouTube Analytics or comments.
        state["result"] = "adapter not configured; no external request made"
        self.database.update_run(run["id"], status="succeeded", node=payload.get("kind", job["type"]), state=state)
        self.events.publish(run["id"], "run.completed", f"{job['type'].title()} cycle recorded in local mode", payload=state)

    def run_once(self) -> bool:
        job = self.database.claim_job()
        if not job:
            return False
        if self.database.setting("kill_switch", "false") == "true" and job["type"] == "production":
            self.database.finish_job(job["id"], "cancelled", "kill switch is active")
            return True
        try:
            if job["type"] == "production":
                self.pipeline.run(job)
            else:
                self.run_auxiliary(job)
            self.database.finish_job(job["id"], "succeeded")
        except Exception as exc:  # noqa: BLE001 - job boundary must retain the error
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
