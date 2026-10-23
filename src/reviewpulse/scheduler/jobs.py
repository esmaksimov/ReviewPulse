"""Background jobs: the nudge tick and the GitLab sync."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import repo
from ..db.models import Review, utcnow
from ..db.session import Database
from ..domain.escalation import policy_from_settings
from ..domain.state import Event
from ..domain.workhours import calendar_from_settings
from ..gitlab.client import GitLabClient
from ..i18n import resolve_locale
from ..services import admin as admin_service
from ..services import gitlab_sync, nudges
from ..services import stats as stats_service
from ..telegram import card, stats_report, texts
from ..telegram.sender import (
    TelegramNudgeSender,
    notify_author_changes_requested,
    notify_reviewer_fixes_done,
)

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

        # GitLab can move either side of the ball on its own: a reviewer reopens a
        # thread after the fixes landed (put it back on the author, same notice as
        # the card's ✍️ button), or every thread gets resolved without anyone
        # touching the bot (put it back on the reviewer, same notice as the card's
        # "fixed" button).
        for change in changes:
            review = await repo.get_review(session, change.assignment.review_id)
            if review is None:
                continue
            if change.event is Event.REQUEST_CHANGES:
                await notify_author_changes_requested(
                    bot,
                    session,
                    review,
                    change.assignment.display_label,
                    settings.default_locale,
                )
            elif change.event is Event.FIXES_DONE:
                await notify_reviewer_fixes_done(
                    bot, session, review, change.assignment, settings.default_locale
                )

    if changes:
        logger.info("gitlab sync applied %s state changes", len(changes))


async def channel_cleanup_tick(bot: Bot, database: Database, settings: Settings) -> None:
    """Delete a closed review's channel post once it's sat past
    `Settings.channel_cleanup_delay` — never right on close, so it's still visible
    for a while. Also how a review closed before this feature ever shipped gets
    caught up: its `closed_at` is already long past the cutoff, so the very first
    tick after deploy sweeps it in along with everything closed since.

    Also catches a review whose card someone has already deleted by hand, regardless
    of the normal delay — there's nothing left to wait for once that's gone (see
    `_reviews_with_a_deleted_card`).
    """
    cutoff = utcnow() - settings.channel_cleanup_delay
    async with database.session() as session:
        due = await repo.closed_reviews_pending_channel_cleanup(session, cutoff)
        card_gone = await _reviews_with_a_deleted_card(bot, session)
        reviews = _dedupe_by_id(due, card_gone)

        done = 0
        abandoned: list[Review] = []
        for review in reviews:
            result = await card.delete_from_channel(bot, review)
            if result is card.ChannelDeleteResult.DELETED:
                review.channel_post_deleted_at = utcnow()
                done += 1
                attachment_ids = await repo.channel_attachment_message_ids(
                    session, review.channel_chat_id, review.channel_message_id
                )
                if attachment_ids:
                    await card.delete_attachments(bot, review.channel_chat_id, attachment_ids)
                    await repo.clear_channel_attachments(
                        session, review.channel_chat_id, review.channel_message_id
                    )
            elif result is card.ChannelDeleteResult.TOO_OLD:
                review.channel_cleanup_abandoned_at = utcnow()
                abandoned.append(review)
            # RETRY: left alone (most likely a missing "Delete messages" right), so
            # the next tick retries it.

        if abandoned:
            await _notify_admins_of_abandoned_cleanups(bot, session, settings, abandoned)

    if reviews:
        logger.info("channel cleanup removed %s/%s post(s)", done, len(reviews))


def _dedupe_by_id(*review_lists: list[Review]) -> list[Review]:
    seen: dict[int, Review] = {}
    for reviews in review_lists:
        for review in reviews:
            seen.setdefault(review.id, review)
    return list(seen.values())


async def _reviews_with_a_deleted_card(bot: Bot, session: AsyncSession) -> list[Review]:
    """Closed reviews whose card has already been deleted by hand — most often
    someone cleaning up a review ahead of (or instead of) the bot's own schedule,
    without realizing the channel post is a separate message in a separate chat that
    still needs deleting too. Checked for every candidate every tick since there's no
    event the Bot API can push when someone else deletes a message — this is the only
    way to notice.
    """
    candidates = await repo.closed_reviews_with_a_card_to_check(session)
    missing = [review for review in candidates if not await card.card_exists(bot, review)]
    if missing:
        logger.info(
            "found %s review(s) with a manually-deleted card, finishing cleanup early",
            len(missing),
        )
    return missing


async def _notify_admins_of_abandoned_cleanups(
    bot: Bot, session: AsyncSession, settings: Settings, reviews: list[Review]
) -> None:
    """One-time DM telling admins which posts need deleting by hand — a real human
    in the Telegram app has no 48-hour limit, only the bot does (see
    `telegram.card.delete_from_channel`). Links straight to the channel post itself
    (`channel_post_url`), not the discussion thread's card — the post is the thing
    that actually needs deleting, and the two are easy to conflate since the card is
    what people interact with day to day."""
    lines = []
    for review in reviews:
        url = card.channel_post_url(review)
        headline = card.headline(review, settings.default_locale)
        lines.append(f"• {headline}\n{url}" if url else f"• {headline}")
    body = "\n".join(lines)

    admins = await admin_service.list_admins_for_display(session, settings)
    for telegram_user_id, _username in admins:
        user = await repo.get_user_by_telegram_id(session, telegram_user_id)
        locale = resolve_locale(user.locale if user else None, default=settings.default_locale)
        text = texts.t(locale, "cleanup_abandoned_notice", count=len(reviews)) + "\n\n" + body
        try:
            await bot.send_message(
                chat_id=telegram_user_id, text=text, disable_web_page_preview=True
            )
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning(
                "could not notify admin %s about abandoned cleanup: %s", telegram_user_id, exc
            )


async def stats_report_tick(bot: Bot, database: Database, settings: Settings) -> None:
    """DM every configured recipient the same digest `/stats` answers on demand.

    A no-op with no recipients configured — mirrors `gitlab_tick`'s
    `gitlab_configured` gate, just on a plain non-empty-list check instead of a flag.
    """
    if not settings.stats_report_recipient_ids:
        return

    until = utcnow()
    since = until - settings.stats_report_interval
    async with database.session() as session:
        transitions = await repo.transitions_between(session, since, until)
        calendar = calendar_from_settings(settings)
        report = stats_service.build_report(transitions, calendar, since=since, until=until)

        sent = 0
        for recipient_id in settings.stats_report_recipient_ids:
            user = await repo.get_user_by_telegram_id(session, recipient_id)
            locale = resolve_locale(user.locale if user else None, default=settings.default_locale)
            text = stats_report.render(report, locale, settings.timezone_offset_hours)
            try:
                await bot.send_message(
                    chat_id=recipient_id, text=text, disable_web_page_preview=True
                )
                sent += 1
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                logger.warning("could not send stats report to %s: %s", recipient_id, exc)

    total = len(settings.stats_report_recipient_ids)
    logger.info("sent stats report to %s/%s recipient(s)", sent, total)


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

    scheduler.add_job(
        channel_cleanup_tick,
        "interval",
        minutes=15,
        args=(bot, database, settings),
        id="channel_cleanup_tick",
        max_instances=1,
        coalesce=True,
    )

    if settings.stats_report_recipient_ids:
        scheduler.add_job(
            stats_report_tick,
            "interval",
            days=settings.stats_report_interval_days,
            args=(bot, database, settings),
            id="stats_report_tick",
            max_instances=1,
            coalesce=True,
        )

    return scheduler
