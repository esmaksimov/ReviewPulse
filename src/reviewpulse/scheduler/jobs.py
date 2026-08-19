"""Background jobs: the nudge tick and the GitLab sync."""

from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..config import Settings
from ..db import repo
from ..db.session import Database
from ..domain.escalation import policy_from_settings
from ..domain.state import Event
from ..domain.workhours import calendar_from_settings
from ..gitlab.client import GitLabClient
from ..services import gitlab_sync, nudges
from ..telegram import card
from ..telegram.sender import TelegramNudgeSender, notify_author_changes_requested

logger = logging.getLogger(__name__)


async def nudge_tick(bot: Bot, database: Database, settings: Settings) -> None:
    policy = policy_from_settings(settings, calendar_from_settings(settings))
    async with database.session() as session:
        sent = await nudges.run_nudge_tick(
            session,
            policy,
            TelegramNudgeSender(bot=bot, session=session, default_locale=settings.default_locale),
        )
    if sent:
        logger.info("sent %s reminders", len(sent))


async def gitlab_tick(bot: Bot, database: Database, settings: Settings) -> None:
    if not settings.gitlab_configured:
        return

    async with GitLabClient(
        base_url=settings.gitlab_base_url,
        token=settings.gitlab_token or "",
        timeout=settings.gitlab_timeout_seconds,
    ) as client, database.session() as session:
        changes = await gitlab_sync.sync_open_reviews(
            session, client, approvals_cap=settings.required_approvals
        )
        # Refresh each affected card once, not once per assignment.
        for review_id in {change.assignment.review_id for change in changes}:
            review = await repo.get_review(session, review_id)
            if review is not None:
                await card.refresh(
                    bot, review, settings.required_approvals, settings.default_locale
                )

        # GitLab can put the ball back on the author on its own (a reviewer reopens a
        # thread after the fixes landed) — same notice as the card's ✍️ button.
        for change in changes:
            if change.event is not Event.REQUEST_CHANGES:
                continue
            review = await repo.get_review(session, change.assignment.review_id)
            if review is not None:
                await notify_author_changes_requested(
                    bot,
                    session,
                    review,
                    change.assignment.display_label,
                    settings.default_locale,
                )

    if changes:
        logger.info("gitlab sync applied %s state changes", len(changes))


def build_scheduler(bot: Bot, database: Database, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Every minute: cheap, and it exits immediately outside working hours.
    scheduler.add_job(
        nudge_tick,
        "interval",
        minutes=1,
        args=(bot, database, settings),
        id="nudge_tick",
        max_instances=1,
        coalesce=True,
    )

    if settings.gitlab_configured:
        scheduler.add_job(
            gitlab_tick,
            "interval",
            minutes=settings.gitlab_poll_minutes,
            args=(bot, database, settings),
            id="gitlab_tick",
            max_instances=1,
            coalesce=True,
        )
    else:
        logger.info("GitLab sync disabled — running on card buttons only")

    return scheduler
