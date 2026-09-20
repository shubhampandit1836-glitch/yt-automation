"""Notification and external dead-man heartbeat adapter."""

from __future__ import annotations

from typing import Any

import httpx


class AlertService:
    def __init__(self, telegram_token: str | None = None, chat_id: str | None = None, heartbeat_url: str | None = None) -> None:
        self.telegram_token = telegram_token
        self.chat_id = chat_id
        self.heartbeat_url = heartbeat_url

    def telegram(self, message: str) -> bool:
        if not self.telegram_token or not self.chat_id:
            return False
        response = httpx.post(
            f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": message[:4096], "disable_web_page_preview": True},
            timeout=20,
        )
        response.raise_for_status()
        return True

    def heartbeat(self, *, success: bool = True) -> bool:
        if not self.heartbeat_url:
            return False
        url = self.heartbeat_url if success else f"{self.heartbeat_url}/fail"
        response = httpx.get(url, timeout=15)
        response.raise_for_status()
        return True

    def publish_success(self, title: str, url: str | None = None) -> dict[str, Any]:
        message = f"Orbit published: {title}" + (f"\n{url}" if url else "")
        return {"telegram": self.telegram(message), "heartbeat": self.heartbeat(success=True)}

    def failure(self, message: str) -> dict[str, Any]:
        return {"telegram": self.telegram(f"Orbit automation failed: {message}"), "heartbeat": self.heartbeat(success=False)}
