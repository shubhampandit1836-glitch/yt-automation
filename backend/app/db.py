"""Small SQLite repository used by the control plane.

SQLite keeps the first install zero-cost and dependency-light. The repository
uses the same explicit tables that a Postgres migration can later implement;
all JSON payloads are kept at the edges so API and graph state stay inspectable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return {} if default is None else default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {} if default is None else default


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    priority INTEGER NOT NULL DEFAULT 50,
    run_after TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, run_after, priority);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    graph TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    current_node TEXT,
    state_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    node TEXT,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id, id);
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'briefed',
    format TEXT NOT NULL,
    content_type TEXT NOT NULL,
    topic TEXT NOT NULL,
    title TEXT,
    slot TEXT,
    duration_seconds REAL,
    script TEXT,
    fact_sheet_json TEXT NOT NULL DEFAULT '[]',
    qa_report_json TEXT NOT NULL DEFAULT '{}',
    license_ledger_json TEXT NOT NULL DEFAULT '[]',
    degradation_level TEXT NOT NULL DEFAULT 'full',
    dry_run INTEGER NOT NULL DEFAULT 1,
    job_id TEXT,
    youtube_video_id TEXT UNIQUE,
    youtube_url TEXT,
    media_path TEXT,
    thumbnail_variants_json TEXT NOT NULL DEFAULT '[]',
    selected_thumbnail TEXT,
    render_manifest_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_created ON videos(created_at DESC);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT,
    captured_at TEXT NOT NULL,
    age_hours REAL NOT NULL DEFAULT 0,
    views INTEGER NOT NULL DEFAULT 0,
    watch_time_minutes REAL NOT NULL DEFAULT 0,
    average_view_percentage REAL,
    ctr REAL,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'mock',
    FOREIGN KEY(video_id) REFERENCES videos(id)
);
CREATE TABLE IF NOT EXISTS series (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pilot',
    bible_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    arms_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT NOT NULL DEFAULT '{}',
    start_at TEXT NOT NULL,
    end_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_health (
    provider TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unknown',
    quota_remaining INTEGER,
    quota_limit INTEGER,
    latency_ms REAL,
    last_checked TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    youtube_comment_id TEXT UNIQUE NOT NULL,
    youtube_video_id TEXT,
    author TEXT,
    text TEXT NOT NULL,
    like_count INTEGER NOT NULL DEFAULT 0,
    label TEXT NOT NULL DEFAULT 'unclassified',
    confidence REAL NOT NULL DEFAULT 0,
    reply_text TEXT,
    replied_at TEXT,
    moderation_status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding_json TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playbook_rules (
    id TEXT PRIMARY KEY,
    component TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    sample_size INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str = "./data/yt_automation.db") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._keeper: sqlite3.Connection | None = None
        self._initialize()

    def _connection(self) -> sqlite3.Connection:
        # A shared in-memory database needs one keeper connection alive.
        if self.path == ":memory:":
            if self._keeper is None:
                self._keeper = sqlite3.connect(
                    "file:yt_automation?mode=memory&cache=shared", uri=True, check_same_thread=False
                )
                self._keeper.row_factory = sqlite3.Row
            connection = sqlite3.connect(
                "file:yt_automation?mode=memory&cache=shared", uri=True, check_same_thread=False
            )
        else:
            connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connection()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if connection is not self._keeper:
                    connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            # Lightweight migrations keep an existing local install usable.
            existing = {row["name"] for row in connection.execute("PRAGMA table_info(videos)").fetchall()}
            migrations = {
                "job_id": "ALTER TABLE videos ADD COLUMN job_id TEXT",
                "youtube_video_id": "ALTER TABLE videos ADD COLUMN youtube_video_id TEXT",
                "youtube_url": "ALTER TABLE videos ADD COLUMN youtube_url TEXT",
                "media_path": "ALTER TABLE videos ADD COLUMN media_path TEXT",
                "thumbnail_variants_json": "ALTER TABLE videos ADD COLUMN thumbnail_variants_json TEXT NOT NULL DEFAULT '[]'",
                "selected_thumbnail": "ALTER TABLE videos ADD COLUMN selected_thumbnail TEXT",
                "render_manifest_json": "ALTER TABLE videos ADD COLUMN render_manifest_json TEXT NOT NULL DEFAULT '{}'",
            }
            for column, statement in migrations.items():
                if column not in existing:
                    connection.execute(statement)
            self._seed(connection)

    @staticmethod
    def _seed(connection: sqlite3.Connection) -> None:
        now = utc_now()
        defaults = {
            "publishing_paused": "false",
            "kill_switch": "false",
            "degradation_level": "full",
        }
        for key, value in defaults.items():
            connection.execute(
                "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )
        provider_rows = [
            ("mock", "reasoning", "healthy", 100, 100),
            ("mock-fast", "classification", "healthy", 100, 100),
            ("mock-tts", "tts", "healthy", 100, 100),
            ("youtube", "youtube", "not_configured", None, None),
        ]
        for provider, role, status, remaining, limit in provider_rows:
            connection.execute(
                """INSERT OR IGNORE INTO provider_health
                (provider, role, status, quota_remaining, quota_limit, last_checked)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (provider, role, status, remaining, limit, now),
            )
        if connection.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 0:
            connection.execute(
                """INSERT INTO series(id, name, state, bible_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    "Myth Busted",
                    "pilot",
                    json_dumps(
                        {
                            "format": "short",
                            "promise": "Fast, sourced gaming myths in Hinglish",
                            "episode_count": 0,
                            "recurring_graphics": ["verdict stamp", "source card"],
                        }
                    ),
                    now,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO experiments(id, name, hypothesis, status, arms_json, start_at)
                VALUES (?, ?, ?, 'running', ?, ?)""",
                (
                    str(uuid.uuid4()),
                    "First hook rotation",
                    "A question-led hook will improve early retention versus a stat-led hook.",
                    json_dumps(
                        [
                            {"name": "question", "traffic_share": 0.5, "samples": 0},
                            {"name": "stat", "traffic_share": 0.5, "samples": 0},
                        ]
                    ),
                    now,
                ),
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def setting(self, key: str, default: str | None = None) -> str | None:
        with self.connection() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str, actor: str = "system") -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, utc_now()),
            )
            connection.execute(
                "INSERT INTO audit_log(actor, action, target, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (actor, "setting.updated", key, json_dumps({"value": value}), utc_now()),
            )

    def create_job(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        *,
        priority: int = 50,
        run_after: str | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        job = {
            "id": str(uuid.uuid4()),
            "type": job_type,
            "payload_json": json_dumps(payload or {}),
            "priority": priority,
            "run_after": run_after or utc_now(),
            "attempts": 0,
            "max_attempts": max_attempts,
            "status": "queued",
            "error": None,
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
        }
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO jobs(id, type, payload_json, priority, run_after, attempts, max_attempts,
                status, error, created_at) VALUES (:id, :type, :payload_json, :priority, :run_after,
                :attempts, :max_attempts, :status, :error, :created_at)""",
                job,
            )
        return job

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)
            ).fetchall()
        return [self._row(row) for row in rows]  # type: ignore[misc]

    def claim_job(self) -> dict[str, Any] | None:
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM jobs WHERE status = 'queued' AND run_after <= ?
                AND attempts < max_attempts ORDER BY priority ASC, run_after ASC LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE jobs SET status = 'running', attempts = attempts + 1, started_at = ?, error = NULL WHERE id = ?",
                (now, row["id"]),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        return self._row(updated)

    def finish_job(self, job_id: str, status: str = "succeeded", error: str | None = None) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, error, utc_now(), job_id),
            )

    def retry_job(self, job_id: str, error: str, delay_seconds: int = 60) -> None:
        run_after = (datetime.now(UTC) + timedelta(seconds=delay_seconds)).isoformat()
        with self.connection() as connection:
            connection.execute(
                "UPDATE jobs SET status='queued', error=?, run_after=? WHERE id=?",
                (error, run_after, job_id),
            )

    def create_run(self, graph: str, job_id: str | None = None, state: dict[str, Any] | None = None) -> dict[str, Any]:
        run = {
            "id": str(uuid.uuid4()),
            "job_id": job_id,
            "graph": graph,
            "status": "queued",
            "current_node": None,
            "state_json": json_dumps(state or {}),
            "error": None,
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
        }
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO runs(id, job_id, graph, status, state_json, created_at)
                VALUES (:id, :job_id, :graph, :status, :state_json, :created_at)""",
                run,
            )
        return run

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        node: str | None = None,
        state: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
            if status == "running":
                fields.append("started_at = COALESCE(started_at, ?)")
                values.append(utc_now())
            if status in {"succeeded", "failed", "cancelled"}:
                fields.append("finished_at = ?")
                values.append(utc_now())
        if node is not None:
            fields.append("current_node = ?")
            values.append(node)
        if state is not None:
            fields.append("state_json = ?")
            values.append(json_dumps(state))
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        if not fields:
            return
        values.append(run_id)
        with self.connection() as connection:
            connection.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", values)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        result = self._row(row)
        if result:
            result["state"] = json_loads(result.pop("state_json"))
        return result

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)
            ).fetchall()
        result = []
        for row in rows:
            item = self._row(row)
            item["state"] = json_loads(item.pop("state_json"))  # type: ignore[union-attr]
            result.append(item)
        return result

    def add_event(
        self,
        run_id: str,
        event_type: str,
        message: str,
        *,
        node: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "run_id": run_id,
            "event_type": event_type,
            "node": node,
            "message": message,
            "payload": payload or {},
            "created_at": utc_now(),
        }
        with self.connection() as connection:
            cursor = connection.execute(
                """INSERT INTO run_events(run_id, event_type, node, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, event_type, node, message, json_dumps(payload or {}), event["created_at"]),
            )
            event["id"] = cursor.lastrowid
        return event

    def list_events(self, run_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as connection:
            if run_id:
                rows = connection.execute(
                    "SELECT * FROM run_events WHERE run_id = ? ORDER BY id DESC LIMIT ?",
                    (run_id, max(1, min(limit, 500))),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM run_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
                ).fetchall()
        result = []
        for row in rows:
            item = self._row(row)
            item["payload"] = json_loads(item.pop("payload_json"))  # type: ignore[union-attr]
            result.append(item)
        return list(reversed(result))

    def create_video(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        video = {
            "id": payload.get("id") or str(uuid.uuid4()),
            "status": payload.get("status", "briefed"),
            "format": payload.get("format", "short"),
            "content_type": payload.get("content_type", "facts"),
            "topic": payload.get("topic", "Untitled topic"),
            "title": payload.get("title"),
            "slot": payload.get("slot"),
            "duration_seconds": payload.get("duration_seconds"),
            "script": payload.get("script"),
            "fact_sheet_json": json_dumps(payload.get("fact_sheet", [])),
            "qa_report_json": json_dumps(payload.get("qa_report", {})),
            "license_ledger_json": json_dumps(payload.get("license_ledger", [])),
            "degradation_level": payload.get("degradation_level", "full"),
            "dry_run": int(payload.get("dry_run", True)),
            "job_id": payload.get("job_id"),
            "youtube_video_id": payload.get("youtube_video_id"),
            "youtube_url": payload.get("youtube_url"),
            "media_path": payload.get("media_path"),
            "thumbnail_variants_json": json_dumps(payload.get("thumbnail_variants", [])),
            "selected_thumbnail": payload.get("selected_thumbnail"),
            "render_manifest_json": json_dumps(payload.get("render_manifest", {})),
            "created_at": now,
            "updated_at": now,
            "published_at": payload.get("published_at"),
        }
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO videos(id, status, format, content_type, topic, title, slot, duration_seconds,
                script, fact_sheet_json, qa_report_json, license_ledger_json, degradation_level, dry_run,
                job_id, youtube_video_id, youtube_url, media_path, thumbnail_variants_json, selected_thumbnail,
                render_manifest_json, created_at, updated_at, published_at)
                VALUES (:id, :status, :format, :content_type, :topic, :title, :slot, :duration_seconds,
                :script, :fact_sheet_json, :qa_report_json, :license_ledger_json, :degradation_level,
                :dry_run, :job_id, :youtube_video_id, :youtube_url, :media_path, :thumbnail_variants_json,
                :selected_thumbnail, :render_manifest_json, :created_at, :updated_at, :published_at)""",
                video,
            )
        return self._hydrate_video(video)

    @staticmethod
    def _hydrate_video(video: dict[str, Any]) -> dict[str, Any]:
        item = dict(video)
        for key, target in (
            ("fact_sheet_json", "fact_sheet"),
            ("qa_report_json", "qa_report"),
            ("license_ledger_json", "license_ledger"),
            ("thumbnail_variants_json", "thumbnail_variants"),
            ("render_manifest_json", "render_manifest"),
        ):
            if key in item:
                item[target] = json_loads(item.pop(key), [] if "ledger" in target or "sheet" in target or "thumbnail" in target else {})
        item["dry_run"] = bool(item.get("dry_run"))
        return item

    def update_video(self, video_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "status",
            "title",
            "slot",
            "duration_seconds",
            "script",
            "degradation_level",
            "published_at",
            "job_id",
            "youtube_video_id",
            "youtube_url",
            "media_path",
            "thumbnail_variants",
            "selected_thumbnail",
            "render_manifest",
            "fact_sheet",
            "qa_report",
            "license_ledger",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            db_key = {
                "fact_sheet": "fact_sheet_json",
                "qa_report": "qa_report_json",
                "license_ledger": "license_ledger_json",
                "thumbnail_variants": "thumbnail_variants_json",
                "render_manifest": "render_manifest_json",
            }.get(key, key)
            assignments.append(f"{db_key} = ?")
            values.append(json_dumps(value) if db_key.endswith("_json") else value)
        if not assignments:
            return self.get_video(video_id)
        assignments.append("updated_at = ?")
        values.append(utc_now())
        values.append(video_id)
        with self.connection() as connection:
            connection.execute(f"UPDATE videos SET {', '.join(assignments)} WHERE id = ?", values)
        return self.get_video(video_id)

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return self._hydrate_video(self._row(row)) if row else None  # type: ignore[arg-type]

    def get_video_by_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM videos WHERE job_id = ?", (job_id,)).fetchone()
        return self._hydrate_video(self._row(row)) if row else None  # type: ignore[arg-type]

    def get_video_by_youtube_id(self, youtube_video_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM videos WHERE youtube_video_id = ?", (youtube_video_id,)
            ).fetchone()
        return self._hydrate_video(self._row(row)) if row else None  # type: ignore[arg-type]

    def list_videos(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM videos ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)
            ).fetchall()
        return [self._hydrate_video(self._row(row)) for row in rows]  # type: ignore[arg-type]

    def clear_preview_records(self) -> dict[str, int]:
        """Remove only local demo/preview records after a real channel connects."""
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT id FROM videos WHERE dry_run = 1 AND youtube_video_id IS NULL"
            ).fetchall()
            video_ids = [row["id"] for row in rows]
            if video_ids:
                placeholders = ",".join("?" for _ in video_ids)
                connection.execute(f"DELETE FROM metrics WHERE video_id IN ({placeholders})", video_ids)
                connection.execute(f"DELETE FROM videos WHERE id IN ({placeholders})", video_ids)
            connection.execute(
                "DELETE FROM series WHERE name = 'Myth Busted' AND json_extract(bible_json, '$.episode_count') = 0"
            )
            connection.execute("DELETE FROM experiments WHERE name = 'First hook rotation' AND json_extract(arms_json, '$[0].samples') = 0")
            connection.execute("DELETE FROM run_events")
            connection.execute("DELETE FROM runs")
            connection.execute("DELETE FROM jobs")
            connection.execute("DELETE FROM memory_items")
        return {"videos_removed": len(video_ids), "demo_seed_removed": 1}

    def add_metric(self, video_id: str | None, values: dict[str, Any]) -> dict[str, Any]:
        data = {
            "video_id": video_id,
            "captured_at": values.get("captured_at", utc_now()),
            "age_hours": values.get("age_hours", 0),
            "views": values.get("views", 0),
            "watch_time_minutes": values.get("watch_time_minutes", 0),
            "average_view_percentage": values.get("average_view_percentage"),
            "ctr": values.get("ctr"),
            "likes": values.get("likes", 0),
            "comments": values.get("comments", 0),
            "source": values.get("source", "mock"),
        }
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM metrics WHERE video_id IS ? AND captured_at = ? AND source = ? LIMIT 1",
                (video_id, data["captured_at"], data["source"]),
            ).fetchone()
            if existing:
                connection.execute(
                    """UPDATE metrics SET age_hours=?, views=?, watch_time_minutes=?, average_view_percentage=?,
                    ctr=?, likes=?, comments=? WHERE id=?""",
                    (data["age_hours"], data["views"], data["watch_time_minutes"], data["average_view_percentage"], data["ctr"], data["likes"], data["comments"], existing["id"]),
                )
                data["id"] = existing["id"]
                return data
            cursor = connection.execute(
                """INSERT INTO metrics(video_id, captured_at, age_hours, views, watch_time_minutes,
                average_view_percentage, ctr, likes, comments, source)
                VALUES (:video_id, :captured_at, :age_hours, :views, :watch_time_minutes,
                :average_view_percentage, :ctr, :likes, :comments, :source)""",
                data,
            )
            data["id"] = cursor.lastrowid
        return data

    def list_metrics(self, video_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as connection:
            if video_id:
                rows = connection.execute(
                    "SELECT * FROM metrics WHERE video_id = ? ORDER BY captured_at DESC LIMIT ?",
                    (video_id, max(1, min(limit, 500))),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM metrics ORDER BY captured_at DESC LIMIT ?", (max(1, min(limit, 500)),)
                ).fetchall()
        return [self._row(row) for row in rows]  # type: ignore[misc]

    def create_series(self, name: str, promise: str, format_name: str) -> dict[str, Any]:
        series_id = str(uuid.uuid4())
        now = utc_now()
        bible = {"format": format_name, "promise": promise, "episode_count": 0, "recurring_graphics": []}
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO series(id, name, state, bible_json, created_at, updated_at) VALUES (?, ?, 'pilot', ?, ?, ?)",
                (series_id, name, json_dumps(bible), now, now),
            )
        return {"id": series_id, "name": name, "state": "pilot", "bible": bible, "created_at": now, "updated_at": now}

    def list_series(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM series ORDER BY updated_at DESC").fetchall()
        result = []
        for row in rows:
            item = self._row(row)
            item["bible"] = json_loads(item.pop("bible_json"))  # type: ignore[union-attr]
            result.append(item)
        return result

    def list_experiments(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM experiments ORDER BY start_at DESC").fetchall()
        result = []
        for row in rows:
            item = self._row(row)
            item["arms"] = json_loads(item.pop("arms_json"), [])  # type: ignore[union-attr]
            item["result"] = json_loads(item.pop("result_json"))  # type: ignore[union-attr]
            result.append(item)
        return result

    def upsert_comment(
        self,
        *,
        youtube_comment_id: str,
        youtube_video_id: str | None,
        author: str | None,
        text: str,
        like_count: int,
        label: str,
        confidence: float,
    ) -> dict[str, Any]:
        now = utc_now()
        comment = {
            "id": str(uuid.uuid4()),
            "youtube_comment_id": youtube_comment_id,
            "youtube_video_id": youtube_video_id,
            "author": author,
            "text": text,
            "like_count": like_count,
            "label": label,
            "confidence": confidence,
            "created_at": now,
            "updated_at": now,
        }
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO comments(id, youtube_comment_id, youtube_video_id, author, text, like_count, label, confidence, created_at, updated_at)
                VALUES (:id, :youtube_comment_id, :youtube_video_id, :author, :text, :like_count, :label, :confidence, :created_at, :updated_at)
                ON CONFLICT(youtube_comment_id) DO UPDATE SET author=excluded.author, text=excluded.text,
                like_count=excluded.like_count, label=excluded.label, confidence=excluded.confidence, updated_at=excluded.updated_at""",
                comment,
            )
        return comment

    def mark_comment_replied(self, youtube_comment_id: str, reply_text: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE comments SET reply_text = ?, replied_at = ?, updated_at = ? WHERE youtube_comment_id = ?",
                (reply_text, utc_now(), utc_now(), youtube_comment_id),
            )

    def list_comments(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM comments ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [self._row(row) for row in rows]  # type: ignore[misc]

    def add_memory_item(self, *, kind: str, title: str, content: str, embedding: list[float] | None = None, metadata: dict[str, Any] | None = None) -> str:
        item_id = str(uuid.uuid4())
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO memory_items(id, kind, title, content, embedding_json, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, kind, title, content, json_dumps(embedding or []), json_dumps(metadata or {}), now, now),
            )
        return item_id

    def update_provider_health(
        self,
        provider: str,
        *,
        status: str,
        role: str = "external",
        error: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO provider_health(provider, role, status, last_checked, error, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET role=excluded.role, status=excluded.status,
                last_checked=excluded.last_checked, error=excluded.error, latency_ms=excluded.latency_ms""",
                (provider, role, status, utc_now(), error, latency_ms),
            )

    def provider_health(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM provider_health ORDER BY role, provider").fetchall()
        return [self._row(row) for row in rows]  # type: ignore[misc]

    def record_audit(self, actor: str, action: str, target: str | None = None, payload: dict[str, Any] | None = None) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO audit_log(actor, action, target, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (actor, action, target, json_dumps(payload or {}), utc_now()),
            )

    def list_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)
            ).fetchall()
        result = []
        for row in rows:
            item = self._row(row)
            item["payload"] = json_loads(item.pop("payload_json"))  # type: ignore[union-attr]
            result.append(item)
        return result

    def overview(self) -> dict[str, Any]:
        with self.connection() as connection:
            video_count = connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
            published_count = connection.execute(
                "SELECT COUNT(*) FROM videos WHERE status IN ('published', 'scheduled', 'simulated')"
            ).fetchone()[0]
            queued = connection.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'").fetchone()[0]
            running = connection.execute("SELECT COUNT(*) FROM jobs WHERE status = 'running'").fetchone()[0]
            failed = connection.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'").fetchone()[0]
            latest_metric = connection.execute(
                "SELECT COALESCE(SUM(views), 0) AS views, COALESCE(SUM(likes), 0) AS likes FROM metrics"
            ).fetchone()
        return {
            "today": {"shorts": 0, "long_form": 0, "published": published_count},
            "videos_total": video_count,
            "queue": {"queued": queued, "running": running, "failed": failed},
            "views": latest_metric["views"],
            "likes": latest_metric["likes"],
            "publishing_paused": self.setting("publishing_paused", "false") == "true",
            "kill_switch": self.setting("kill_switch", "false") == "true",
            "degradation_level": self.setting("degradation_level", "full"),
            "providers": self.provider_health(),
            "heartbeat": {"status": "not_configured", "last_ping": None},
        }
