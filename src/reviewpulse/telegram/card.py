"""Rendering and refreshing the tracker card that lives in the comments thread."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Review
from ..services.reviews import approvals_needed
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
    """(label, url) pairs — `project!42` for a GitLab MR, `project#42` for a GitHub
    PR, matching each platform's own convention."""
    pairs = []
    for link in review.merge_requests:
        name = link.project_path.rsplit("/", 1)[-1]
        separator = "#" if link.platform == "github" else "!"
        pairs.append((f"{name}{separator}{link.iid}", link.web_url))
    return pairs


def render(review: Review, approvals_cap: int, locale: str) -> tuple[str, object]:
    """`approvals_cap` is the configured ceiling (REQUIRED_APPROVALS); the number
    actually shown and enforced scales down to the number of named reviewers — see
    `services.reviews.approvals_needed`. `locale` is `Settings.default_locale`: the
    card is shared, so it cannot follow any one reviewer's language."""
    rows = [
        (row.display_label, row.state) for row in review.assignments if row.removed_at is None
    ]
    text = texts.card(
        locale,
        headline=headline(review, locale),
        rows=rows,
        is_closed=review.is_closed,
        approvals=review.approvals,
        required_approvals=approvals_needed(review, approvals_cap),
        merge_requests=merge_request_pairs(review),
        unparsed_reviewers=not rows,
    )
    markup = keyboards.review_card(
        review.id, locale, is_closed=review.is_closed, needs_reviewers=not rows
    )
    return text, markup


async def publish(
    bot: Bot, session: AsyncSession, review: Review, approvals_cap: int, locale: str
) -> None:
    """Post the card into the post's comment thread, or refresh it if it exists.

    Requires the auto-forwarded copy in the linked discussion group: replying to it is
    what places the card inside the thread rather than loose in the group.
    """
    if review.discussion_chat_id is None or review.discussion_message_id is None:
        logger.info(
            "review %s has no discussion-thread link yet, card not published", review.id
        )
        return

    text, markup = render(review, approvals_cap, locale)

    if review.card_message_id is not None:
        await refresh(bot, review, approvals_cap, locale)
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


async def refresh(bot: Bot, review: Review, approvals_cap: int, locale: str) -> None:
    if review.discussion_chat_id is None or review.card_message_id is None:
        return

    text, markup = render(review, approvals_cap, locale)
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


def headline(review: Review, locale: str) -> str:
    parts = [part for part in (review.product, review.title) if part]
    return " — ".join(parts) if parts else texts.t(locale, "default_headline")
