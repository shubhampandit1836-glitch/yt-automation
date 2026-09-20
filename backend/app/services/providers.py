"""Provider interfaces and a deterministic safe-mode router.

No provider is called until its API key is configured. In dry-run the mock
provider produces explicit, source-bearing fixtures so development and tests do
not burn free-tier quotas or fabricate a successful upload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .governor import BudgetReservation, ResourceGovernor

try:  # Keep LangChain thin: it normalizes invocation, not business policy.
    from langchain_core.runnables import RunnableLambda
except ImportError:  # pragma: no cover - core dependencies include this in normal installs
    RunnableLambda = None  # type: ignore[assignment]


class TextModel(Protocol):
    def generate(self, prompt: str, *, purpose: str) -> dict[str, Any]: ...


@dataclass
class MockTextModel:
    name: str = "mock-gemini-compatible"

    def generate(self, prompt: str, *, purpose: str) -> dict[str, Any]:
        return {
            "provider": self.name,
            "purpose": purpose,
            "text": "Bhai, yeh demo output hai — real provider key configure hone par hi external call hogi.",
            "mock": True,
        }


class ProviderRouter:
    def __init__(self, governor: ResourceGovernor, *, dry_run: bool = True) -> None:
        self.governor = governor
        self.dry_run = dry_run
        self.mock = MockTextModel()

    def generate(self, prompt: str, *, role: str = "reasoning", purpose: str = "generation") -> dict[str, Any]:
        reservation: BudgetReservation | None = self.governor.reserve(role, 1)
        if reservation is None:
            raise RuntimeError(f"No healthy {role} provider has available budget")
        try:
            # Real adapters belong here. They must return a normalized object,
            # retain model/latency/token metadata, and never silently fall back.
            return self.mock.generate(prompt, purpose=purpose)
        finally:
            self.governor.release(reservation)

    def as_runnable(self, *, role: str = "reasoning", purpose: str = "generation") -> Any:
        """Expose the router through a LangChain Runnable without hiding quota use."""
        if RunnableLambda is None:
            return lambda prompt: self.generate(prompt, role=role, purpose=purpose)
        return RunnableLambda(lambda prompt: self.generate(prompt, role=role, purpose=purpose))

    def health(self) -> dict[str, Any]:
        return {
            "mode": "dry-run" if self.dry_run else "configured-adapters",
            "providers": self.governor.provider_status(),
        }
