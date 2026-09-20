"""A conservative resource governor for free-tier providers.

The initial implementation persists provider health in SQLite and reserves a
small in-process budget. The interface is intentionally independent from a
provider SDK so swapping SQLite for Postgres and adding real quota APIs does
not change graph nodes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from ..db import Database, utc_now


@dataclass(frozen=True)
class BudgetReservation:
    provider: str
    role: str
    units: int
    reservation_id: str


class ResourceGovernor:
    PRIORITY = {"publish": 1, "production": 2, "monitor": 3, "learning": 4}

    def __init__(self, database: Database) -> None:
        self.database = database
        self._lock = threading.Lock()
        self._reserved: dict[str, int] = {}
        self._counter = 0

    def provider_status(self) -> list[dict[str, Any]]:
        return self.database.provider_health()

    def choose(self, role: str, candidates: list[str] | None = None) -> str | None:
        allowed = set(candidates or [])
        providers = [item for item in self.provider_status() if item["role"] == role]
        if allowed:
            providers = [item for item in providers if item["provider"] in allowed]
        healthy = [item for item in providers if item["status"] in {"healthy", "degraded"}]
        healthy.sort(key=lambda item: (item["status"] != "healthy", item["quota_remaining"] or 0), reverse=False)
        return healthy[0]["provider"] if healthy else None

    def reserve(self, role: str, units: int = 1, *, candidates: list[str] | None = None) -> BudgetReservation | None:
        if units < 1:
            raise ValueError("units must be positive")
        with self._lock:
            provider = self.choose(role, candidates)
            if not provider:
                return None
            status = next(item for item in self.provider_status() if item["provider"] == provider)
            remaining = status.get("quota_remaining")
            if remaining is not None and remaining - self._reserved.get(provider, 0) < units:
                return None
            self._reserved[provider] = self._reserved.get(provider, 0) + units
            self._counter += 1
            return BudgetReservation(provider, role, units, f"reservation-{self._counter}")

    def release(self, reservation: BudgetReservation, *, used: bool = True) -> None:
        with self._lock:
            current = self._reserved.get(reservation.provider, 0)
            self._reserved[reservation.provider] = max(0, current - reservation.units)
        if used:
            self._consume_persisted_quota(reservation)

    def _consume_persisted_quota(self, reservation: BudgetReservation) -> None:
        # The mock provider has a finite daily allowance. Real adapters can
        # replace this with a provider console/API read without changing callers.
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT quota_remaining FROM provider_health WHERE provider = ?",
                (reservation.provider,),
            ).fetchone()
            if row and row["quota_remaining"] is not None:
                connection.execute(
                    "UPDATE provider_health SET quota_remaining = MAX(0, quota_remaining - ?), last_checked = ? WHERE provider = ?",
                    (reservation.units, utc_now(), reservation.provider),
                )

    def snapshot(self) -> dict[str, Any]:
        rows = self.provider_status()
        return {
            "providers": rows,
            "reservations": dict(self._reserved),
            "priority": self.PRIORITY,
        }
