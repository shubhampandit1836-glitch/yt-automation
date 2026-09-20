"""Durable event writes plus an in-process SSE fan-out."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from .db import Database


class EventBus:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(
        self,
        run_id: str,
        event_type: str,
        message: str,
        *,
        node: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = self.database.add_event(
            run_id, event_type, message, node=node, payload=payload or {}
        )
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # The durable event log is the source of truth. A slow browser
                # should not back up a worker or make the pipeline fail.
                pass
        return event

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
