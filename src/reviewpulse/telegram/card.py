"""Rendering and refreshing the tracker card that lives in the comments thread."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Review
from . import keyboards, texts

logger = logging.getLogger(__name__)


def review_url(review: Review) -> str | None:
    """Deep link to the post. Only public channels have a /c/ style web link, so we
    build the discussion-group one, which is what people actually want to open."""
    if review.discussion_chat_id is None or review.discussion_message_id is None:
        return None
    internal = str(review.discussion_chat_id).removeprefix("-100")
    return f"https://t.me/c/{internal}/{review.discussion_message_id}"


def merge_request_pairs(review: Review) -> list[tuple[str, str]]:
    return [
        (f"{link.project_path.rsplit('/', 1)[-1]}!{link.iid}", link.web_url)
        for link in review.merge_requests
    ]


def render(review: Review, required_approvals: int) -> tuple[str, object]:
    rows = [(row.display_label, row.state) for row in review.assignments]
    text = texts.card(
        headline=_headline(review),
        rows=rows,
        is_closed=review.is_closed,
        approvals=review.approvals,
        required_approvals=required_approvals,
        merge_requests=merge_request_pairs(review),
        unparsed_reviewers=not rows,
    )
    markup = keyboards.review_card(
        review.id, is_closed=review.is_closed, needs_reviewers=not rows
    )
    return text, markup


async def publish(
    bot: Bot, session: AsyncSession, review: Review, required_approvals: int
) -> None:
    """Post the card into the post's comment thread, or refresh it if it exists.

    Requires the auto-forwarded copy in the linked discussion group: replying to it is
    what places the card inside the thread rather than loose in the group.
    """
    if review.discussion_chat_id is None or review.discussion_message_id is None:
        return

    text, markup = render(review, required_approvals)

    if review.card_message_id is not None:
        await refresh(bot, review, required_approvals)
        return

    message = await bot.send_message(
        chat_id=review.discussion_chat_id,
        text=text,
        reply_markup=markup,
        reply_to_message_id=review.discussion_message_id,
        disable_web_page_preview=True,
    )
    review.card_message_id = message.message_id
    await session.flush()


async def refresh(bot: Bot, review: Review, required_approvals: int) -> None:
    if review.discussion_chat_id is None or review.card_message_id is None:
        return

    text, markup = render(review, required_approvals)
    try:
        await bot.edit_message_text(
            chat_id=review.discussion_chat_id,
            message_id=review.card_message_id,
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        # Telegram rejects an edit that would not change anything — that is fine, it
        # happens whenever a no-op event replays.
        if "message is not modified" not in str(exc):
            logger.warning("could not refresh card for review %s: %s", review.id, exc)


def _headline(review: Review) -> str:
    parts = [part for part in (review.product, review.title) if part]
    return " — ".join(parts) if parts else "Ревью"
