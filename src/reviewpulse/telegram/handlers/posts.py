"""Turning a channel post into a tracked review.

Two updates describe the same post and arrive in an unpredictable order:

  1. `channel_post` — the text, from the channel itself.
  2. `message` with `is_automatic_forward=True` in the linked discussion group — the
     copy Telegram makes so the post can have comments. Its `forward_origin.message_id`
     points back at (1).

Only (2) gives us somewhere to reply, and only (1) is guaranteed to carry the full text,
so both handlers upsert the same row and then try to publish the card. Whichever lands
second is the one that actually posts it.

Both are also handled on *edit*, not just on first arrival: a post that didn't parse
as a review yet (missing a label the parser didn't know, say) can be turned into one
retroactively just by editing it in the channel — no restart or manual command needed,
as long as the bot is already running the code that understands the new label.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...parsing.post_parser import parse_post
from ...services import reviews as review_service
from .. import card, texts

logger = logging.getLogger(__name__)

router = Router(name="posts")


@router.channel_post(F.text)
@router.edited_channel_post(F.text)
async def on_channel_post(
    message: Message, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    post = parse_post(message.text or "", message.entities)
    if not post.looks_like_review:
        return  # announcements and chatter carry no MR link

    review = await review_service.create_or_update_review(
        session,
        channel_chat_id=message.chat.id,
        channel_message_id=message.message_id,
        post=post,
        raw_text=message.text or "",
        posted_at=message.date,
        author_label=message.author_signature,
    )
    await card.publish(bot, session, review, settings.required_approvals, settings.default_locale)
    await _warn_unreachable_reviewers(bot, session, review, settings.default_locale)


@router.message(F.is_automatic_forward, F.forward_origin)
@router.edited_message(F.is_automatic_forward, F.forward_origin)
async def on_discussion_copy(
    message: Message, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    """The auto-forwarded copy: this is the anchor the card replies to."""
    origin = message.forward_origin
    channel_chat_id = getattr(getattr(origin, "chat", None), "id", None)
    channel_message_id = getattr(origin, "message_id", None)
    if channel_chat_id is None or channel_message_id is None:
        return

    text = message.text or message.caption or ""
    post = parse_post(text, message.entities or message.caption_entities)
    if not post.looks_like_review:
        return

    review = await review_service.create_or_update_review(
        session,
        channel_chat_id=channel_chat_id,
        channel_message_id=channel_message_id,
        post=post,
        raw_text=text,
        posted_at=message.date,
    )
    review.discussion_chat_id = message.chat.id
    review.discussion_message_id = message.message_id
    await session.flush()

    await card.publish(bot, session, review, settings.required_approvals, settings.default_locale)
    await _warn_unreachable_reviewers(bot, session, review, settings.default_locale)


async def _warn_unreachable_reviewers(
    bot: Bot, session: AsyncSession, review, locale: str
) -> None:
    """A reviewer we have no telegram id for cannot be DMed at all.

    Silently doing nothing would look exactly like a working bot, so say it once, in
    the thread, and remember that we did. Posted in the shared thread, so it follows
    the deployment's default locale rather than any one reviewer's.
    """
    unreachable = [
        row
        for row in review.assignments
        if row.telegram_user_id is None and not row.registration_hint_sent
    ]
    if not unreachable or review.discussion_chat_id is None:
        return

    me = await bot.me()
    try:
        await bot.send_message(
            chat_id=review.discussion_chat_id,
            text=texts.registration_hint(
                locale, [row.display_label for row in unreachable], me.username
            ),
            reply_to_message_id=review.discussion_message_id,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.warning("could not post registration hint for review %s", review.id, exc_info=True)
        return

    for row in unreachable:
        row.registration_hint_sent = True
    await session.flush()
