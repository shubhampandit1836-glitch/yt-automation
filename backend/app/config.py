"""Environment-backed application settings.

The application deliberately defaults to a dry-run and to the local SQLite
store. That makes the control plane useful before a cloud database or provider
account is configured, without ever pretending that a simulated publish was a
real YouTube upload.
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
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: Annotated[list[str], BeforeValidator(_split_origins)] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    enable_scheduler: bool = False
    dry_run: bool = True
    owner_email: str = "owner@example.com"
    heartbeat_url: str | None = Field(default=None, validation_alias="HEALTHCHECKS_HEARTBEAT_URL")
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
