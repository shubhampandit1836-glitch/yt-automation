"""Comment intelligence: conservative heuristics plus optional Gemini labels."""

from __future__ import annotations

import re
import uuid
from typing import Any

from ..db import Database, utc_now
from .providers import ProviderRouter
from .youtube import YouTubeService

SPAM_TERMS = ("sub4sub", "subscribe back", "whatsapp", "telegram", "crypto", "free money")
TOXIC_TERMS = ("kill yourself", "idiot", "stupid", "hate you")


class CommentService:
    def __init__(self, database: Database, youtube: YouTubeService, router: ProviderRouter, *, replies_enabled: bool, max_replies: int = 3) -> None:
        self.database = database
        self.youtube = youtube
        self.router = router
        self.replies_enabled = replies_enabled
        self.max_replies = max_replies

    def classify(self, text: str) -> tuple[str, float]:
        normalized = text.lower()
        if "http://" in normalized or "https://" in normalized or any(term in normalized for term in SPAM_TERMS):
            return "spam", 0.98
        if any(term in normalized for term in TOXIC_TERMS):
            return "toxic", 0.96
        if any(term in normalized for term in ("part 2", "part-2", "next video", "make a video", "please cover")):
            return "request", 0.9
        if any(term in normalized for term in ("wrong", "source", "fact", "actually", "correction")):
            return "factual_correction", 0.82
        if any(term in normalized for term in ("voice", "pacing", "subtitle", "audio", "editing")):
            return "genuine_feedback", 0.85
        if len(normalized.split()) >= 3:
            return "genuine_feedback", 0.68
        return "neutral", 0.7

    def run_cycle(self, video_ids: list[str]) -> dict[str, Any]:
        fetched = 0
        replied = 0
        for video_id in video_ids:
            youtube_id = self.database.get_video(video_id)
            if not youtube_id or not youtube_id.get("youtube_video_id"):
                continue
            for item in self.youtube.list_comments(youtube_id["youtube_video_id"]):
                comment_id = item["id"]
                snippet = item.get("snippet", {})
                text = snippet.get("textDisplay") or snippet.get("textOriginal") or ""
                label, confidence = self.classify(text)
                self.database.upsert_comment(
                    youtube_comment_id=comment_id,
                    youtube_video_id=youtube_id["youtube_video_id"],
                    author=snippet.get("authorDisplayName"),
                    text=text,
                    like_count=int(snippet.get("likeCount", 0)),
                    label=label,
                    confidence=confidence,
                )
                fetched += 1
                if self.replies_enabled and label in {"request", "genuine_feedback"} and confidence >= 0.85 and replied < self.max_replies:
                    reply = "Thanks bhai! Is request ko channel ke next ideas mein add kar raha hoon."
                    self.youtube.reply_to_comment(comment_id, reply)
                    self.database.mark_comment_replied(comment_id, reply)
                    replied += 1
        return {"comments_fetched": fetched, "replies_posted": replied}
