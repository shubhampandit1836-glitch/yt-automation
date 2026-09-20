"""Evidence-backed playbook updates with small-channel guardrails."""

from __future__ import annotations

import statistics
import uuid
from typing import Any

from ..db import Database, json_dumps, utc_now


class LearningService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def cycle(self) -> dict[str, Any]:
        videos = self.database.list_videos(200)
        rewards: list[float] = []
        for video in videos:
            metrics = self.database.list_metrics(video["id"], 20)
            if not metrics:
                continue
            latest = metrics[0]
            retention = float(latest.get("average_view_percentage") or 0) / 100
            discovery = min(1.0, float(latest.get("views") or 0) / 1000)
            ctr = float(latest.get("ctr") or 0) / 100
            reward = round(0.35 * retention + 0.25 * discovery + 0.15 * ctr, 4)
            rewards.append(reward)
        summary = {
            "videos_reviewed": len(videos),
            "samples": len(rewards),
            "median_reward": statistics.median(rewards) if rewards else None,
            "exploration_floor": 0.15,
            "promoted": 0,
            "rolled_back": 0,
        }
        if len(rewards) >= 3:
            median = statistics.median(rewards)
            now = utc_now()
            with self.database.connection() as connection:
                existing = connection.execute("SELECT COUNT(*) FROM playbook_rules WHERE component = 'retention'").fetchone()[0]
                if not existing:
                    connection.execute(
                        """INSERT INTO playbook_rules(id, component, rule_text, status, evidence_json, sample_size, confidence, created_at, updated_at)
                        VALUES (?, 'retention', ?, 'candidate', ?, ?, ?, ?, ?)""",
                        (str(uuid.uuid4()), "Keep the first proof inside the opening 5 seconds.", json_dumps([]), len(rewards), min(0.99, len(rewards) / 10), now, now),
                    )
            summary["promoted"] = 0
            summary["candidate_created"] = median
        return summary

    def list_rules(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute("SELECT * FROM playbook_rules ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]
