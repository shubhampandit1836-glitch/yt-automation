"""Provider interfaces and real Gemini/mock routing.

All calls are normalized here so graph nodes do not know provider SDK details.
When ``DRY_RUN`` is true, no network call is made and every response is marked
as mock data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .governor import BudgetReservation, ResourceGovernor

try:  # Keep LangChain thin: it normalizes invocation, not business policy.
    from langchain_core.runnables import RunnableLambda
except ImportError:  # pragma: no cover
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


class GeminiTextModel:
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, prompt: str, *, purpose: str) -> dict[str, Any]:
        response = httpx.post(
            self.endpoint.format(model=self.model),
            params={"key": self.api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.55, "responseMimeType": "application/json" if "JSON only" in prompt else "text/plain"},
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts).strip()
        result: dict[str, Any] = {"provider": f"gemini/{self.model}", "purpose": purpose, "text": text, "mock": False}
        if "JSON only" in prompt:
            cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.IGNORECASE).strip()
            try:
                result["json"] = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Gemini returned invalid JSON for a structured task") from exc
        return result


class ProviderRouter:
    def __init__(
        self,
        governor: ResourceGovernor,
        *,
        dry_run: bool = True,
        gemini_api_key: str | None = None,
        gemini_model: str = "gemini-2.5-flash",
    ) -> None:
        self.governor = governor
        self.dry_run = dry_run
        self.mock = MockTextModel()
        self.gemini = GeminiTextModel(gemini_api_key, gemini_model) if gemini_api_key else None
        if self.gemini:
            self.governor.database.update_provider_health("gemini", status="healthy", role="reasoning")

    def generate(self, prompt: str, *, role: str = "reasoning", purpose: str = "generation") -> dict[str, Any]:
        if not self.dry_run and self.gemini is None:
            raise RuntimeError("GEMINI_API_KEY is required when DRY_RUN=false")
        candidates = ["mock"] if self.dry_run else ["gemini"]
        reservation: BudgetReservation | None = self.governor.reserve(role, 1, candidates=candidates)
        if reservation is None:
            raise RuntimeError(f"No healthy {role} provider has available budget")
        try:
            if self.dry_run:
                return self.mock.generate(prompt, purpose=purpose)
            return self.gemini.generate(prompt, purpose=purpose)  # type: ignore[union-attr]
        finally:
            self.governor.release(reservation)

    def as_runnable(self, *, role: str = "reasoning", purpose: str = "generation") -> Any:
        """Expose the router through a LangChain Runnable without hiding quota use."""
        if RunnableLambda is None:
            return lambda prompt: self.generate(prompt, role=role, purpose=purpose)
        return RunnableLambda(lambda prompt: self.generate(prompt, role=role, purpose=purpose))

    def health(self) -> dict[str, Any]:
        return {
            "mode": "dry-run" if self.dry_run else "provider-backed",
            "providers": self.governor.provider_status(),
            "gemini_configured": bool(self.gemini),
        }
