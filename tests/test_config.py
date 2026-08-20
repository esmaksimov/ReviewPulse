"""Settings parsing — thin, but it is the first thing that runs on every deploy."""

from __future__ import annotations

from datetime import time, timedelta
from pathlib import Path

import pytest

from reviewpulse.config import Settings


def load(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_comma_separated_lists_are_accepted() -> None:
    """People write WORK_DAYS=0,1,2,3,4 — not JSON. pydantic-settings would try to
    JSON-decode a list-typed field before any validator saw it."""
    settings = load(BOT_TOKEN="t", WORK_DAYS="0,1,2,3,4", ADMIN_USER_IDS="42, 77")

    assert settings.work_days == [0, 1, 2, 3, 4]
    assert settings.admin_user_ids == [42, 77]


def test_empty_list_values_are_accepted() -> None:
    """`.env.example` ships ADMIN_USER_IDS= with nothing after it."""
    settings = load(BOT_TOKEN="t", ADMIN_USER_IDS="")
    assert settings.admin_user_ids == []


def test_times_are_parsed_from_hh_mm() -> None:
    settings = load(BOT_TOKEN="t", WORK_START="09:30", WORK_END="18:00")
    assert settings.work_start == time(9, 30)
    assert settings.work_end == time(18, 0)


def test_durations_are_exposed_as_timedeltas() -> None:
    settings = load(BOT_TOKEN="t", SLA_MINUTES="90", NUDGE_INTERVAL_MINUTES="15")
    assert settings.sla == timedelta(minutes=90)
    assert settings.nudge_interval == timedelta(minutes=15)


def test_gitlab_needs_both_the_flag_and_a_token() -> None:
    assert not load(BOT_TOKEN="t").gitlab_configured
    assert not load(BOT_TOKEN="t", GITLAB_ENABLED="true").gitlab_configured
    assert not load(BOT_TOKEN="t", GITLAB_TOKEN="x").gitlab_configured
    assert load(BOT_TOKEN="t", GITLAB_ENABLED="true", GITLAB_TOKEN="x").gitlab_configured


def test_missing_bot_token_is_a_startup_error() -> None:
    with pytest.raises(ValueError):
        load()


def test_the_shipped_env_example_actually_loads() -> None:
    """The file every deploy starts from must not blow up on first run."""
    example = Path(__file__).resolve().parents[1] / ".env.example"
    settings = Settings(_env_file=example)  # type: ignore[call-arg]

    assert settings.work_days == [0, 1, 2, 3, 4]
    assert settings.admin_user_ids == []
    assert settings.work_start == time(9, 0)
    assert settings.required_approvals == 2
    assert not settings.gitlab_configured


# --- logging ----------------------------------------------------------------
#
# Migrations run in-process at startup, and alembic's env.py hands alembic.ini to
# `fileConfig`, which pins the root logger to WARNING and installs its own handler.
# Left alone, that outlives the migration step and the bot logs nothing for the rest
# of the process — which is exactly why an HTML-escaping crash in /status left no
# trace in `docker logs` and had to be diagnosed from the database instead.


def test_logging_survives_the_config_alembic_leaves_behind() -> None:
    import logging

    from reviewpulse.__main__ import configure_logging

    root = logging.getLogger()
    original_level, original_handlers = root.level, root.handlers[:]
    try:
        # What fileConfig(alembic.ini) leaves behind.
        root.handlers = [logging.NullHandler()]
        root.setLevel(logging.WARNING)

        configure_logging(load(BOT_TOKEN="t", LOG_LEVEL="INFO"))

        assert root.level == logging.INFO, "an INFO deploy must not be left at WARNING"
        assert root.handlers, "the app needs a handler of its own, not alembic's"
        assert not any(isinstance(h, logging.NullHandler) for h in root.handlers)
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_log_level_setting_is_honoured() -> None:
    import logging

    from reviewpulse.__main__ import configure_logging

    root = logging.getLogger()
    original_level, original_handlers = root.level, root.handlers[:]
    try:
        configure_logging(load(BOT_TOKEN="t", LOG_LEVEL="WARNING"))
        assert root.level == logging.WARNING
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)
