"""Backend scheduler: enqueue only, never render or call a provider."""

from __future__ import annotations

import argparse

from .config import get_settings
from .db import Database

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError:  # pragma: no cover
    BlockingScheduler = CronTrigger = IntervalTrigger = None  # type: ignore[assignment]


class AutonomousScheduler:
    def __init__(self, database: Database | None = None) -> None:
        settings = get_settings()
        self.settings = settings
        self.database = database or Database(settings.database_path)
        self.scheduler = None

    def enqueue(self, job_type: str, payload: dict | None = None, priority: int = 50) -> None:
        if self.database.setting("kill_switch", "false") == "true" and job_type == "production":
            return
        self.database.create_job(job_type, payload or {}, priority=priority)

    def install_jobs(self) -> None:
        if BlockingScheduler is None:
            raise RuntimeError("APScheduler is not installed; install the base project dependencies")
        # A production Postgres deployment should wrap this in pg_try_advisory_lock.
        # SQLite uses one scheduler process by convention in the compose stack.
        self.scheduler = BlockingScheduler(timezone="Asia/Kolkata")
        self.scheduler.add_job(
            lambda: self.enqueue("ops", {"kind": "watchdog"}, priority=5),
            IntervalTrigger(minutes=15),
            id="ops-watchdog",
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            lambda: self.enqueue("research", {"kind": "trend-radar"}, priority=40),
            IntervalTrigger(hours=2),
            id="trend-radar",
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            lambda: self.enqueue("comments", {"kind": "comment-cycle"}, priority=60),
            IntervalTrigger(hours=4),
            id="comment-cycle",
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            lambda: self.enqueue("learning", {"kind": "daily-learning"}, priority=80),
            IntervalTrigger(hours=24),
            id="daily-learning",
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            lambda: self.enqueue("monitor", {"kind": "velocity-watch"}, priority=30),
            IntervalTrigger(hours=1),
            id="velocity-watch",
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            lambda: self.enqueue("monitor", {"kind": "youtube-sync"}, priority=25),
            IntervalTrigger(hours=1),
            id="youtube-sync",
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            lambda: self.enqueue("monitor", {"kind": "analytics-deep"}, priority=35),
            IntervalTrigger(hours=24),
            id="analytics-deep",
            coalesce=True,
            max_instances=1,
        )
        # Planning only creates a production job. The worker owns the graph.
        self.scheduler.add_job(
            lambda: self.enqueue(
                "production",
                {"format": "short", "content_type": "facts", "topic": ""},
                priority=20,
            ),
            CronTrigger(hour=0, minute=30),
            id="daily-short-production",
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            lambda: self.enqueue(
                "production",
                {"format": "long", "content_type": "facts", "topic": ""},
                priority=25,
            ),
            CronTrigger(day_of_week="sun", hour=12, minute=30),
            id="weekly-long-production",
            coalesce=True,
            max_instances=1,
        )

    def serve(self) -> None:
        self.install_jobs()
        assert self.scheduler is not None
        self.scheduler.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the autonomous channel scheduler")
    parser.add_argument("--once", action="store_true", help="enqueue one local production job")
    args = parser.parse_args()
    scheduler = AutonomousScheduler()
    if args.once:
        scheduler.enqueue("production", {"format": "short", "content_type": "facts"}, priority=20)
    elif get_settings().enable_scheduler:
        scheduler.serve()
    else:
        raise SystemExit("ENABLE_SCHEDULER=false; set it to true to start the scheduler")


if __name__ == "__main__":
    main()
