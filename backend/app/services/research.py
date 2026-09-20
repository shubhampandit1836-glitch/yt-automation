"""Grounded topic and fact research from RSS plus Gemini JSON generation."""

from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from .providers import ProviderRouter


@dataclass
class SourceItem:
    title: str
    url: str
    summary: str


class ResearchService:
    def __init__(self, router: ProviderRouter, rss_urls: list[str], youtube: Any | None = None) -> None:
        self.router = router
        self.rss_urls = rss_urls
        self.youtube = youtube

    @staticmethod
    def _clean(value: str | None) -> str:
        return re.sub(r"\s+", " ", html.unescape(value or "")).strip()

    def collect_sources(self, *, limit_per_feed: int = 8) -> list[SourceItem]:
        sources: list[SourceItem] = []
        for url in self.rss_urls:
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "OrbitChannelBot/1.0"})
                with urllib.request.urlopen(request, timeout=12) as response:  # noqa: S310 - URLs are configured by owner
                    root = ET.fromstring(response.read())
                for item in root.findall(".//item")[:limit_per_feed]:
                    title = self._clean(item.findtext("title"))
                    link = self._clean(item.findtext("link"))
                    summary = self._clean(item.findtext("description"))[:600]
                    if title and link:
                        sources.append(SourceItem(title, link, summary))
            except Exception:
                # One broken RSS feed cannot block the whole radar.
                continue
        return sources

    def choose_topic(self) -> str:
        sources = self.collect_sources()
        if not sources:
            raise RuntimeError("No research feeds returned a topic")
        return sources[0].title

    def research(self, topic: str, content_type: str = "facts") -> list[dict[str, Any]]:
        sources = self.collect_sources()
        related = [item for item in sources if topic.lower() in item.title.lower() or not topic]
        if len(related) < 2:
            related = sources[:8]
        if len(related) < 2:
            raise RuntimeError("Research gate needs at least two independent source records")
        source_text = "\n".join(
            f"SOURCE {index}: {item.title}\nURL: {item.url}\nSUMMARY: {item.summary}"
            for index, item in enumerate(related, start=1)
        )
        prompt = f"""
You are the fact-checking researcher for a Hinglish gaming channel.
Topic: {topic}
Content type: {content_type}
Use only the source records below. Do not use memory. Return JSON only in this shape:
{{"facts":[{{"claim":"short factual claim","sources":["URL1","URL2"],"verified":true}}]}}
A claim is verified only when at least two distinct source URLs support it. Drop speculation,
rumours, unsupported numbers, and claims about people. Keep claims suitable for an advertiser-friendly video.

{source_text}
"""
        result = self.router.generate(prompt, role="reasoning", purpose="grounded-research")
        facts = result.get("json", {}).get("facts") if isinstance(result.get("json"), dict) else None
        if not isinstance(facts, list):
            raise RuntimeError("Research provider did not return the required fact-sheet JSON")
        allowed = {item.url for item in related}
        cleaned: list[dict[str, Any]] = []
        for item in facts:
            if not isinstance(item, dict):
                continue
            source_urls = [url for url in item.get("sources", []) if url in allowed]
            if item.get("claim") and len(set(source_urls)) >= 2:
                cleaned.append({"claim": str(item["claim"]).strip(), "sources": source_urls, "verified": True, "topic": topic})
        if not cleaned:
            raise RuntimeError("No independently verified claims survived the research gate")
        return cleaned
