"""Optional Groq Whisper word timing adapter with deterministic fallback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx


class AlignmentService:
    def __init__(self, groq_api_key: str | None = None) -> None:
        self.groq_api_key = groq_api_key

    def align(self, audio_path: str | Path, script: str, duration: float) -> list[dict[str, Any]]:
        if self.groq_api_key:
            with open(audio_path, "rb") as audio:
                response = httpx.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.groq_api_key}"},
                    files={"file": (Path(audio_path).name, audio, "audio/mpeg")},
                    data={"model": "whisper-large-v3-turbo", "response_format": "verbose_json", "timestamp_granularities[]": "word"},
                    timeout=120,
                )
            response.raise_for_status()
            words = response.json().get("words", [])
            if words:
                return [{"word": item.get("word", "").strip(), "start": item.get("start", 0), "end": item.get("end", 0)} for item in words]
        tokens = re.findall(r"\S+", script)
        step = duration / max(1, len(tokens))
        return [{"word": word, "start": index * step, "end": (index + 1) * step} for index, word in enumerate(tokens)]
