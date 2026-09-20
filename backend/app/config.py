"""Environment-backed application settings.

The application defaults to a dry-run and a local SQLite store. Real channel
automation is opt-in: it needs OAuth, a grounded research provider, a renderer,
and an explicit ``DRY_RUN=false`` setting.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_origins(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "YT Automation Control Plane"
    app_env: str = "development"
    database_path: str = "./data/yt_automation.db"
    media_dir: str = "./data/media"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: Annotated[list[str], BeforeValidator(_split_origins)] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    enable_scheduler: bool = False
    dry_run: bool = True
    owner_email: str = "owner@example.com"

    # Provider configuration. Empty values are safe and do not trigger a call.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    groq_api_key: str | None = None
    tts_voice: str = "hi-IN-SwaraNeural"
    rss_urls: str = (
        "https://news.google.com/rss/search?q=gaming&hl=en-IN&gl=IN&ceid=IN:en,"
        "https://www.pcgamer.com/rss/,https://www.eurogamer.net/feed"
    )

    # YouTube OAuth files must live outside Git. A personal OAuth client works
    # for one channel once the consent screen is published to Production.
    youtube_client_secret_file: str = "secrets/youtube-client.json"
    youtube_token_file: str = "secrets/youtube-token.json"
    youtube_redirect_uri: str = "http://localhost:8000/api/v1/auth/youtube/callback"
    youtube_channel_id: str | None = None

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    heartbeat_url: str | None = Field(default=None, validation_alias="HEALTHCHECKS_HEARTBEAT_URL")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def rss_url_list(self) -> list[str]:
        return [item.strip() for item in self.rss_urls.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
