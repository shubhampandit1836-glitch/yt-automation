"""Long-term memory with optional Gemini embeddings and local cosine fallback."""

from __future__ import annotations

import math
import uuid
from typing import Any

import httpx

from ..db import Database, json_dumps, json_loads, utc_now


class MemoryService:
    def __init__(self, database: Database, api_key: str | None = None, model: str = "gemini-embedding-001") -> None:
        self.database = database
        self.api_key = api_key
        self.model = model

    def _embed(self, text: str) -> list[float] | None:
        if not self.api_key:
            return None
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:embedContent",
            params={"key": self.api_key},
            json={"content": {"parts": [{"text": text}]}},
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("embedding", {}).get("values")

    def upsert(self, *, kind: str, title: str, content: str, metadata: dict[str, Any] | None = None) -> str:
        now = utc_now()
        embedding = self._embed(f"{title}\n{content}")
        item_id = str(uuid.uuid4())
        with self.database.connection() as connection:
            connection.execute(
                """INSERT INTO memory_items(id, kind, title, content, embedding_json, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (item_id, kind, title, content, json_dumps(embedding or []), json_dumps(metadata or {}), now, now),
            )
        return item_id

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        size = min(len(left), len(right))
        dot = sum(left[i] * right[i] for i in range(size))
        left_norm = math.sqrt(sum(value * value for value in left[:size]))
        right_norm = math.sqrt(sum(value * value for value in right[:size]))
        return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        query_embedding = self._embed(query) if query.strip() else None
        with self.database.connection() as connection:
            rows = connection.execute("SELECT * FROM memory_items ORDER BY updated_at DESC LIMIT 500").fetchall()
        result = []
        query_words = set(query.lower().split())
        for row in rows:
            item = dict(row)
            item["metadata"] = json_loads(item.pop("metadata_json"), {})
            item_embedding = json_loads(item.pop("embedding_json"), [])
            lexical = len(query_words & set(f"{item['title']} {item['content']}".lower().split())) / max(1, len(query_words))
            semantic = self._cosine(query_embedding or [], item_embedding)
            item["score"] = round(max(semantic, lexical), 4)
            result.append(item)
        result.sort(key=lambda item: (item["score"], item["updated_at"]), reverse=True)
        return result[:limit]
