"""/myposts: reassign a reviewer on an already-published, still-open post — the
"they went on vacation" case. Open to everyone, scoped to posts naming the tapping
user as "Автор:" (`repo.open_reviews_authored_by`) — the same visibility boundary
`/status` already uses for "what's on you right now", just the mirror image: "what
you're waiting on others for."

No FSM involved: every step here is a one-shot button tap (open a review, pick a
reviewer to replace, pick their replacement), so a chain of stateless callback
handlers is enough, the same shape `handlers.admin`'s remove flows already use.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...db import repo
from ...db.models import Review
from ...i18n import resolve_locale
from ...services import admin as admin_service
from ...services import reviews as review_service
from .. import card, keyboards, texts
from ..keyboards import MENU_MYPOSTS_TEXTS, MyPostReplaceStart, MyPostReplaceWith, MyPostSelect

router = Router(name="myposts")
router.message.filter(F.chat.type == "private")


async def _locale_for(session: AsyncSession, user: User, settings: Settings) -> str:
    row = await repo.upsert_user(
        session, user.id, user.username, user.full_name, user.language_code
    )
    return resolve_locale(row.locale, user.language_code, default=settings.default_locale)


def _authored_by(review: Review, username: str | None) -> bool:
    return (
        bool(username)
        and bool(review.author_username)
        and review.author_username.lower() == username.lower()
    )


@router.message(Command("myposts"))
@router.message(F.text.in_(MENU_MYPOSTS_TEXTS))
async def on_myposts(message: Message, session: AsyncSession, settings: Settings) -> None:
    locale = await _locale_for(session, message.from_user, settings)
    if not message.from_user.username:
        await message.answer(texts.t(locale, "myposts_no_username"))
        return

    reviews = await repo.open_reviews_authored_by(session, message.from_user.username)
    if not reviews:
        await message.answer(texts.t(locale, "myposts_empty"))
        return

    lines = [texts.t(locale, "myposts_header")]
    buttons: list[tuple[int, str]] = []
    for review in reviews:
        headline = card.headline(review, locale)
        needed = review_service.approvals_needed(review, settings.required_approvals)
        lines.append(
            texts.t(
                locale,
                "myposts_review_line",
                headline=headline,
                approved=review.approvals,
                needed=needed,
            )
        )
        buttons.append((review.id, headline))

    await message.answer("\n".join(lines), reply_markup=keyboards.myposts_list(buttons))


@router.callback_query(MyPostSelect.filter())
async def on_myposts_select(
    query: CallbackQuery, callback_data: MyPostSelect, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, query.from_user, settings)
    review = await repo.get_review(session, callback_data.review_id)
    if review is None or review.is_closed or not _authored_by(review, query.from_user.username):
        await query.answer(texts.t(locale, "myposts_gone"), show_alert=True)
        return

    active = [row for row in review.assignments if row.removed_at is None]
    rows = [
        (row.id, f"{row.display_label} — {texts.state_label(locale, row.state)}")
        for row in active
    ]
    await query.message.answer(
        texts.t(locale, "myposts_manage_header", headline=card.headline(review, locale)),
        reply_markup=keyboards.myposts_reviewer_list(rows),
    )
    await query.answer()


@router.callback_query(MyPostReplaceStart.filter())
async def on_myposts_replace_start(
    query: CallbackQuery,
    callback_data: MyPostReplaceStart,
    session: AsyncSession,
    settings: Settings,
) -> None:
    locale = await _locale_for(session, query.from_user, settings)
    assignment = await repo.get_assignment_by_id(session, callback_data.assignment_id)
    review = assignment.review if assignment else None
    if (
        assignment is None
        or review is None
        or review.is_closed
        or assignment.removed_at is not None
        or not _authored_by(review, query.from_user.username)
    ):
        await query.answer(texts.t(locale, "myposts_gone"), show_alert=True)
        return

    excluded = {(review.author_username or "").lower()}
    excluded.update(
        (row.username or "").lower() for row in review.assignments if row.removed_at is None
    )
    pool = await admin_service.pool_usernames(session, review.product or "")
    candidates = [name for name in pool if name.lower() not in excluded]
    if not candidates:
        await query.answer(texts.t(locale, "myposts_no_candidates"), show_alert=True)
        return

    await query.message.answer(
        texts.t(locale, "myposts_pick_replacement", current=assignment.display_label),
        reply_markup=keyboards.myposts_candidate_list(callback_data.assignment_id, candidates),
    )
    await query.answer()


@router.callback_query(MyPostReplaceWith.filter())
async def on_myposts_replace_with(
    query: CallbackQuery,
    callback_data: MyPostReplaceWith,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
) -> None:
    locale = await _locale_for(session, query.from_user, settings)
    assignment = await repo.get_assignment_by_id(session, callback_data.assignment_id)
    review = assignment.review if assignment else None
    if (
        assignment is None
        or review is None
        or review.is_closed
        or assignment.removed_at is not None
        or not _authored_by(review, query.from_user.username)
    ):
        await query.answer(texts.t(locale, "myposts_gone"), show_alert=True)
        return

    old_label = assignment.display_label
    await review_service.replace_reviewer(
        session, review, assignment, new_username=callback_data.username
    )
    await card.refresh(bot, review, settings.required_approvals, settings.default_locale)
    await query.answer(
        texts.t(locale, "myposts_replaced", old=old_label, new=callback_data.username),
        show_alert=True,
    )
