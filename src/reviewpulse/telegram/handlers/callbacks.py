"""Button presses — the bot's only trustworthy source of "who did what".

Every press carries `from_user`, so we also take the opportunity to register the user
and backfill their telegram id onto assignments that so far only knew a @handle.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...db import repo
from ...db.models import ReviewerAssignment, utcnow
from ...domain.state import Event, ReviewerState
from ...domain.workhours import calendar_from_settings
from ...i18n import resolve_locale
from ...services import nudges as nudge_service
from ...services import reviews as review_service
from .. import card, sender, texts
from ..keyboards import ReviewAction, SnoozeAction

logger = logging.getLogger(__name__)

router = Router(name="callbacks")


@router.callback_query(ReviewAction.filter())
async def on_review_action(
    query: CallbackQuery,
    callback_data: ReviewAction,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    user = query.from_user
    user_row = await repo.upsert_user(
        session, user.id, user.username, user.full_name, user.language_code
    )
    if user.username:
        await review_service.link_user_to_assignments(session, user.id, user.username)
        await review_service.link_author_to_reviews(session, user.id, user.username)
    locale = resolve_locale(user_row.locale, user.language_code, default=settings.default_locale)

    review = await repo.get_review(session, callback_data.review_id)
    if review is None:
        await query.answer(texts.t(locale, "review_gone"), show_alert=True)
        return

    handler = {
        "approve": _approve,
        "changes": _request_changes,
        "fixed": _fixes_done,
        "close": _close,
        "claim": _claim,
    }[callback_data.action]

    answer = await handler(session, review, user, bot, settings, locale)
    await card.refresh(bot, review, settings.required_approvals, settings.default_locale)
    await query.answer(answer, show_alert=False)


async def _approve(
    session: AsyncSession, review, user, bot: Bot, settings: Settings, locale: str
) -> str:
    return await _verdict(session, review, user, bot, Event.APPROVE, settings, locale)


async def _request_changes(
    session: AsyncSession, review, user, bot: Bot, settings: Settings, locale: str
) -> str:
    return await _verdict(session, review, user, bot, Event.REQUEST_CHANGES, settings, locale)


async def _verdict(
    session: AsyncSession,
    review,
    user,
    bot: Bot,
    event: Event,
    settings: Settings,
    locale: str,
) -> str:
    assignment = await repo.find_assignment(session, review.id, user.id)
    if assignment is None:
        return texts.t(locale, "not_a_reviewer")

    result = await review_service.apply_verdict(
        session, assignment, event, approvals_cap=settings.required_approvals
    )
    if not result.changed:
        return texts.t(locale, "answer_already", state=texts.state_label(locale, assignment.state))

    if event is Event.REQUEST_CHANGES:
        await sender.notify_author_changes_requested(
            bot, session, review, assignment.display_label, settings.default_locale
        )
    if result.review_closed:
        await sender.notify_author_review_approved(bot, session, review, settings.default_locale)
        return texts.t(locale, "answer_closed_by_approval")
    return texts.t(locale, "answer_approved" if event is Event.APPROVE else "answer_changes")


async def _fixes_done(
    session: AsyncSession, review, user, bot: Bot, settings: Settings, locale: str
) -> str:
    """The author reports the comments are addressed — hand the ball back."""
    moved = await review_service.mark_fixes_done(session, review)
    if not moved:
        return texts.t(locale, "answer_nothing_to_fix")
    for assignment in moved:
        await sender.notify_reviewer_fixes_done(
            bot, session, review, assignment, settings.default_locale
        )
    return texts.t(locale, "answer_fixed")


async def _close(
    session: AsyncSession, review, user, bot: Bot, settings: Settings, locale: str
) -> str:
    closed = await review_service.close_review(session, review)
    return texts.t(locale, "answer_review_closed" if closed else "answer_already_closed")


async def _claim(
    session: AsyncSession, review, user, bot: Bot, settings: Settings, locale: str
) -> str:
    """Fallback when the post's reviewer line could not be parsed."""
    existing = await repo.find_assignment(session, review.id, user.id)
    if existing is not None:
        return texts.t(locale, "answer_already_reviewer")

    review.assignments.append(
        ReviewerAssignment(
            mention_key=f"id:{user.id}",
            username=user.username,
            telegram_user_id=user.id,
            display_label=f"@{user.username}" if user.username else user.full_name,
            state=ReviewerState.PENDING,
            ball_since=utcnow(),
        )
    )
    await session.flush()
    return texts.t(locale, "answer_now_reviewer")


@router.callback_query(SnoozeAction.filter())
async def on_snooze(
    query: CallbackQuery, callback_data: SnoozeAction, session: AsyncSession, settings: Settings
) -> None:
    user = query.from_user
    user_row = await repo.upsert_user(
        session, user.id, user.username, user.full_name, user.language_code
    )
    locale = resolve_locale(user_row.locale, user.language_code, default=settings.default_locale)

    assignment = await session.get(ReviewerAssignment, callback_data.assignment_id)
    if assignment is None or assignment.telegram_user_id != user.id:
        await query.answer(texts.t(locale, "review_gone"), show_alert=True)
        return

    now = utcnow()
    if callback_data.hours:
        until = now + timedelta(hours=callback_data.hours)
        label = texts.t(locale, "snoozed_hour")
    else:
        # "Until tomorrow" is the next work-window opening, not a flat 24 hours —
        # over a weekend that means Monday morning.
        until = calendar_from_settings(settings).next_window_start_after(now)
        label = texts.t(locale, "snoozed_tomorrow")

    await nudge_service.snooze(session, assignment, until)
    await query.answer(label, show_alert=False)
