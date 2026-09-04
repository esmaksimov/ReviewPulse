"""Runtime configuration, loaded from environment / .env."""

from __future__ import annotations

from datetime import time, timedelta
from functools import lru_cache
from typing import Annotated

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: List fields are annotated with NoDecode so the raw string reaches our validators.
#: Without it pydantic-settings JSON-decodes anything list-typed first, and the
#: comma-separated values people naturally write ("0,1,2,3,4") — or an empty value —
#: blow up before a validator ever sees them.
IntList = Annotated[list[int], NoDecode]


class ProjectReviewConfig(BaseModel):
    """One GitLab project's setup for `/announce` — see `services.announcements`.

    Keyed in `Settings.review_projects` by the same `project_path` slug
    `parsing.gitlab_url` already extracts from an MR URL, so resolving "which config
    applies" needs no new parsing.
    """

    product: str
    #: Always included when set (and not the composer themself) — the "someone senior
    #: always reviews this" slot. Optional: a project with none just draws every
    #: reviewer from `pool`.
    techlead: str | None = None
    pool: list[str] = Field(default_factory=list)
    #: Total reviewers on the generated post, techlead included. Mirrors
    #: REQUIRED_APPROVALS's own default (2) but is set per project, independently.
    reviewer_count: int = 2


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
    #: Ceiling on how many 👍 a review needs. The actual requirement scales down to the
    #: number of reviewers named in the post — list one and their approval is enough,
    #: list two and both must sign off — this only caps it for longer reviewer lists.
    required_approvals: int = Field(default=2, alias="REQUIRED_APPROVALS")
    #: How long a closed review's post stays visible in the channel before the bot
    #: removes it — not immediate, so people still glance at it right after closing.
    channel_cleanup_delay_hours: int = Field(default=4, alias="CHANNEL_CLEANUP_DELAY_HOURS")

    # --- GitLab (feature-flagged; off until a token is issued) --------------
    gitlab_enabled: bool = Field(default=False, alias="GITLAB_ENABLED")
    gitlab_base_url: str = Field(default="https://gitlab.example.com", alias="GITLAB_BASE_URL")
    gitlab_token: str | None = Field(default=None, alias="GITLAB_TOKEN")
    gitlab_poll_minutes: int = Field(default=5, alias="GITLAB_POLL_MINUTES")
    gitlab_timeout_seconds: float = Field(default=10.0, alias="GITLAB_TIMEOUT_SECONDS")

    # --- /announce (optional; empty means the command replies "not configured") ----
    #: JSON object, keyed by project_path: {"product", "techlead", "pool", "reviewer_count"}.
    #: Deliberately the one setting in this file that *wants* pydantic-settings' default
    #: JSON decoding — unlike the comma-lists above, this is structured data, and never
    #: belongs anywhere but a real .env: no roster of names is ever committed here.
    review_projects: dict[str, ProjectReviewConfig] = Field(
        default_factory=dict, alias="REVIEW_PROJECTS"
    )

    # --- Stats report (optional; empty recipient list means the tick is a no-op) ---
    #: Numeric Telegram ids that get the periodic who's-slow digest — same shape as
    #: ADMIN_USER_IDS, not tied to it: a recipient doesn't need to be a bot admin, and
    #: an admin doesn't automatically get the report.
    stats_report_recipient_ids: IntList = Field(
        default_factory=list, alias="STATS_REPORT_RECIPIENT_IDS"
    )
    stats_report_interval_days: int = Field(default=7, alias="STATS_REPORT_INTERVAL_DAYS")

    # --- Language -------------------------------------------------------------
    # Locale for messages with no single owner: the shared tracker card and the
    # registration hint. DMs use each reviewer's own locale instead — see
    # telegram/i18n.py — so this does not have to match everyone's language.
    default_locale: str = Field(default="en", alias="DEFAULT_LOCALE")

    # --- Storage ------------------------------------------------------------
    database_url: str = Field(default="sqlite+aiosqlite:///./reviewpulse.db", alias="DATABASE_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("default_locale")
    @classmethod
    def _validate_locale(cls, value: str) -> str:
        from .i18n import SUPPORTED_LOCALES

        normalized = value.strip().lower()
        if normalized not in SUPPORTED_LOCALES:
            raise ValueError(
                f"DEFAULT_LOCALE must be one of {', '.join(SUPPORTED_LOCALES)}, got {value!r}"
            )
        return normalized

    @field_validator("work_start", "work_end", mode="before")
    @classmethod
    def _coerce_time(cls, value: object) -> object:
        return _parse_hhmm(value) if isinstance(value, str) else value

    @field_validator("work_days", "admin_user_ids", "stats_report_recipient_ids", mode="before")
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
    def channel_cleanup_delay(self) -> timedelta:
        return timedelta(hours=self.channel_cleanup_delay_hours)

    @property
    def gitlab_configured(self) -> bool:
        """GitLab polling only runs when both the flag and a token are present."""
        return self.gitlab_enabled and bool(self.gitlab_token)

    @property
    def stats_report_interval(self) -> timedelta:
        return timedelta(days=self.stats_report_interval_days)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
