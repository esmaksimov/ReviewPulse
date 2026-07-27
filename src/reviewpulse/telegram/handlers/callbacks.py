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
from ...services import nudges as nudge_service
from ...services import reviews as review_service
from .. import card, texts
from ..keyboards import ReviewAction, SnoozeAction

logger = logging.getLogger(__name__)

router = Router(name="callbacks")

_ANSWERS = {
    "approve": "👍 Апрув засчитан",
    "changes": "✍️ Отмечено: нужны правки",
    "fixed": "Ревьюверы уведомлены, что правки готовы",
}


@router.callback_query(ReviewAction.filter())
async def on_review_action(
    query: CallbackQuery, callback_data: ReviewAction, session: AsyncSession,
    bot: Bot, settings: Settings,
) -> None:
    user = query.from_user
    await repo.upsert_user(session, user.id, user.username, user.full_name)
    if user.username:
        await review_service.link_user_to_assignments(session, user.id, user.username)

    review = await repo.get_review(session, callback_data.review_id)
    if review is None:
        await query.answer(texts.REVIEW_GONE, show_alert=True)
        return

    handler = {
        "approve": _approve,
        "changes": _request_changes,
        "fixed": _fixes_done,
        "close": _close,
        "claim": _claim,
    }[callback_data.action]

    answer = await handler(session, review, user, settings)
    await card.refresh(bot, review, settings.required_approvals)
    await query.answer(answer, show_alert=False)


async def _approve(session: AsyncSession, review, user, settings: Settings) -> str:
    return await _verdict(session, review, user, Event.APPROVE, settings)


async def _request_changes(session: AsyncSession, review, user, settings: Settings) -> str:
    return await _verdict(session, review, user, Event.REQUEST_CHANGES, settings)


async def _verdict(session: AsyncSession, review, user, event: Event, settings: Settings) -> str:
    assignment = await repo.find_assignment(session, review.id, user.id)
    if assignment is None:
        return texts.NOT_A_REVIEWER

    result = await review_service.apply_verdict(
        session, assignment, event, required_approvals=settings.required_approvals
    )
    if not result.changed:
        return f"Уже {texts.STATE_LABEL[assignment.state]}"
    if result.review_closed:
        return "👍 Апрув засчитан, ревью закрыто"
    return _ANSWERS["approve" if event is Event.APPROVE else "changes"]


async def _fixes_done(session: AsyncSession, review, user, settings: Settings) -> str:
    """The author reports the comments are addressed — hand the ball back."""
    moved = await review_service.mark_fixes_done(session, review)
    if not moved:
        return "Никто сейчас не ждёт правок по этому ревью"
    return _ANSWERS["fixed"]


async def _close(session: AsyncSession, review, user, settings: Settings) -> str:
    closed = await review_service.close_review(session, review)
    return "Ревью закрыто" if closed else "Ревью уже закрыто"


async def _claim(session: AsyncSession, review, user, settings: Settings) -> str:
    """Fallback when the post's reviewer line could not be parsed."""
    existing = await repo.find_assignment(session, review.id, user.id)
    if existing is not None:
        return "Ты уже ревьювер этого ревью"

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
    return "Теперь ты ревьювер этого ревью"


@router.callback_query(SnoozeAction.filter())
async def on_snooze(
    query: CallbackQuery, callback_data: SnoozeAction, session: AsyncSession, settings: Settings
) -> None:
    assignment = await session.get(ReviewerAssignment, callback_data.assignment_id)
    if assignment is None or assignment.telegram_user_id != query.from_user.id:
        await query.answer(texts.REVIEW_GONE, show_alert=True)
        return

    now = utcnow()
    if callback_data.hours:
        until = now + timedelta(hours=callback_data.hours)
        label = f"Не побеспокою {callback_data.hours} ч"
    else:
        # "Until tomorrow" is the next work-window opening, not a flat 24 hours —
        # over a weekend that means Monday morning.
        until = calendar_from_settings(settings).next_window_start_after(now)
        label = "Не побеспокою до завтра"

    await nudge_service.snooze(session, assignment, until)
    await query.answer(label, show_alert=False)
