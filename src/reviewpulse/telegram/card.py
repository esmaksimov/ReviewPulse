"""Rendering and refreshing the tracker card that lives in the comments thread."""

from __future__ import annotations

import logging
from enum import Enum

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Review
from ..services.reviews import approvals_needed
from . import keyboards, texts

logger = logging.getLogger(__name__)


class ChannelDeleteResult(Enum):
    """What `delete_from_channel` actually accomplished — `channel_cleanup_tick`
    treats each one differently (see there)."""

    #: Gone from the channel now, or already was — nothing left to do.
    DELETED = "deleted"
    #: Telegram refused for a reason that might clear up on its own (most likely a
    #: missing "Delete messages" right) — worth retrying on a later tick.
    RETRY = "retry"
    #: Telegram refused because the post is older than the Bot API's 48-hour
    #: deletion window — permanent, no later tick will ever succeed either.
    TOO_OLD = "too_old"


def review_url(review: Review) -> str | None:
    """Deep link to the post. Only public channels have a /c/ style web link, so we
    build the discussion-group one, which is what people actually want to open."""
    if review.discussion_chat_id is None or review.discussion_message_id is None:
        return None
    internal = str(review.discussion_chat_id).removeprefix("-100")
    return f"https://t.me/c/{internal}/{review.discussion_message_id}"


def channel_post_url(review: Review) -> str | None:
    """Deep link straight to the original channel post — distinct from `review_url`
    (the discussion thread's card). `scheduler.jobs._notify_admins_of_abandoned_cleanups`
    needs this one specifically: the thing that actually needs manual deletion is the
    channel post, not the card, and the two are easy to conflate since the card is
    what people interact with day to day — pointing at the card there instead would
    send an admin to the wrong message entirely."""
    if review.channel_chat_id is None or review.channel_message_id is None:
        return None
    internal = str(review.channel_chat_id).removeprefix("-100")
    return f"https://t.me/c/{internal}/{review.channel_message_id}"


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


async def delete_from_channel(bot: Bot, review: Review) -> ChannelDeleteResult:
    """Remove the original post from the channel — called by
    `scheduler.jobs.channel_cleanup_tick` once a closed review has sat for
    `Settings.channel_cleanup_delay`, never right on close, so the post is still
    visible for a while after it stops being actionable. The discussion thread
    (comments, this very card) is untouched: it stays as the record, only the
    channel listing gets decluttered.

    Requires the bot to be a channel admin with the "Delete messages" right; without
    it Telegram refuses with `RETRY`, so a backlog clears itself once the right is
    granted. A post that's already gone (someone deleted it by hand) counts as
    `DELETED` instead: the end state is the one this function exists to reach, so
    there's nothing to retry — same "no-op event replays are fine" tolerance as
    `refresh`, just returning success instead of silently swallowing it.

    Telegram's Bot API flatly refuses to delete any message older than 48 hours —
    true for every bot, admin rights or not, with no override. A review that takes
    that long to close is rare, but when it does, no amount of retrying ever
    succeeds, so this is told apart from `RETRY` as `TOO_OLD`.
    """
    if review.channel_chat_id is None or review.channel_message_id is None:
        return ChannelDeleteResult.RETRY
    try:
        await bot.delete_message(
            chat_id=review.channel_chat_id, message_id=review.channel_message_id
        )
    except TelegramBadRequest as exc:
        if "message to delete not found" in str(exc):
            return ChannelDeleteResult.DELETED
        if "message can't be deleted" in str(exc):
            logger.warning(
                "channel post for review %s is past Telegram's 48h deletion window, "
                "giving up: %s",
                review.id,
                exc,
            )
            return ChannelDeleteResult.TOO_OLD
        logger.warning("could not delete channel post for review %s: %s", review.id, exc)
        return ChannelDeleteResult.RETRY
    return ChannelDeleteResult.DELETED


async def card_exists(bot: Bot, review: Review) -> bool:
    """Whether the card is still there — someone with delete rights in the
    discussion group can remove it directly, same as any other message, and the Bot
    API gives no event when that happens; a no-op markup edit is the only
    side-effect-free way to check. Used by `scheduler.jobs.channel_cleanup_tick` to
    notice a review a human has already started cleaning up by hand, so the channel
    post doesn't sit there orphaned forever just because nobody told the bot.

    `True` for anything short of Telegram confirming the message is actually gone —
    a transient error reads as "can't tell, assume it's still there", so a hiccup
    never triggers an unwarranted early deletion.
    """
    if review.discussion_chat_id is None or review.card_message_id is None:
        return True
    try:
        await bot.edit_message_reply_markup(
            chat_id=review.discussion_chat_id, message_id=review.card_message_id
        )
    except TelegramBadRequest as exc:
        if "message to edit not found" in str(exc):
            return False
    return True


async def delete_attachments(bot: Bot, chat_id: int, message_ids: list[int]) -> None:
    """Delete the files sent alongside a post, once the post itself is gone — called
    right after `delete_from_channel` returns `DELETED` (see `channel_cleanup_tick`).
    Not attempted for `TOO_OLD`: a file sent moments after its post is just as far
    past the 48-hour window, so there's nothing a retry here could ever accomplish
    either — they're left for the admin to clean up by hand along with the post.
    """
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest as exc:
            if "message to delete not found" not in str(exc):
                logger.warning(
                    "could not delete attachment message %s in %s: %s", message_id, chat_id, exc
                )
