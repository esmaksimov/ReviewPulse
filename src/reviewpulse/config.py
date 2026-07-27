"""Runtime configuration, loaded from environment / .env."""

from __future__ import annotations

from datetime import time, timedelta
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: List fields are annotated with NoDecode so the raw string reaches our validators.
#: Without it pydantic-settings JSON-decodes anything list-typed first, and the
#: comma-separated values people naturally write ("0,1,2,3,4") — or an empty value —
#: blow up before a validator ever sees them.
IntList = Annotated[list[int], NoDecode]


def _parse_hhmm(value: str) -> time:
    hours, _, minutes = value.strip().partition(":")
    return time(hour=int(hours), minute=int(minutes or 0))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram -----------------------------------------------------------
    bot_token: str = Field(alias="BOT_TOKEN")
    channel_id: int | None = Field(default=None, alias="CHANNEL_ID")
    admin_user_ids: IntList = Field(default_factory=list, alias="ADMIN_USER_IDS")

    # --- Working hours (the clock every deadline is measured against) -------
    timezone_offset_hours: int = Field(default=3, alias="TIMEZONE_OFFSET_HOURS")
    work_start: time = Field(default=time(9, 0), alias="WORK_START")
    work_end: time = Field(default=time(18, 0), alias="WORK_END")
    # 0 = Monday .. 6 = Sunday
    work_days: IntList = Field(default_factory=lambda: [0, 1, 2, 3, 4], alias="WORK_DAYS")

    # --- Escalation ---------------------------------------------------------
    sla_minutes: int = Field(default=120, alias="SLA_MINUTES")
    recheck_sla_minutes: int = Field(default=120, alias="RECHECK_SLA_MINUTES")
    nudge_interval_minutes: int = Field(default=20, alias="NUDGE_INTERVAL_MINUTES")
    max_nudges_per_day: int = Field(default=8, alias="MAX_NUDGES_PER_DAY")
    required_approvals: int = Field(default=2, alias="REQUIRED_APPROVALS")

    # --- GitLab (feature-flagged; off until a token is issued) --------------
    gitlab_enabled: bool = Field(default=False, alias="GITLAB_ENABLED")
    gitlab_base_url: str = Field(default="https://gitlab.example.com", alias="GITLAB_BASE_URL")
    gitlab_token: str | None = Field(default=None, alias="GITLAB_TOKEN")
    gitlab_poll_minutes: int = Field(default=5, alias="GITLAB_POLL_MINUTES")
    gitlab_timeout_seconds: float = Field(default=10.0, alias="GITLAB_TIMEOUT_SECONDS")

    # --- Storage ------------------------------------------------------------
    database_url: str = Field(default="sqlite+aiosqlite:///./reviewpulse.db", alias="DATABASE_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("work_start", "work_end", mode="before")
    @classmethod
    def _coerce_time(cls, value: object) -> object:
        return _parse_hhmm(value) if isinstance(value, str) else value

    @field_validator("work_days", "admin_user_ids", mode="before")
    @classmethod
    def _coerce_int_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(" ", "").split(",") if part]
        return value

    @property
    def sla(self) -> timedelta:
        return timedelta(minutes=self.sla_minutes)

    @property
    def recheck_sla(self) -> timedelta:
        return timedelta(minutes=self.recheck_sla_minutes)

    @property
    def nudge_interval(self) -> timedelta:
        return timedelta(minutes=self.nudge_interval_minutes)

    @property
    def gitlab_configured(self) -> bool:
        """GitLab polling only runs when both the flag and a token are present."""
        return self.gitlab_enabled and bool(self.gitlab_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
