"""`/announce`: generate the pinned-template post instead of typing it by hand.

DM-only, deliberately: a DM gives a real `from_user`, unlike an anonymous channel
post, so the author is known for free — and because the bot ends up posting the
result itself, it can manage that post afterwards, which it could never do with a
human's own post (see `services.announcements` and `telegram.announcement` for why).
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...db import repo
from ...db.models import AnnouncementDraft
from ...i18n import resolve_locale
from ...parsing.post_parser import parse_post
from ...services import announcements
from .. import announcement, texts
from ..keyboards import MENU_ANNOUNCE_TEXTS, AnnounceAction

logger = logging.getLogger(__name__)

router = Router(name="announce")
router.message.filter(F.chat.type == "private")


@router.message(Command("announce"))
async def on_announce(
    message: Message, command: CommandObject, session: AsyncSession, settings: Settings
) -> None:
    user = message.from_user
    user_row = await repo.upsert_user(
        session, user.id, user.username, user.full_name, user.language_code
    )
    locale = resolve_locale(user_row.locale, user.language_code, default=settings.default_locale)

    if not user.username:
        await message.answer(texts.t(locale, "announce_no_username"))
        return
    if not command.args:
        await message.answer(texts.t(locale, "announce_usage"))
        return

    # No `entities`: their offsets are relative to the full message including the
    # "/announce " prefix, and this parse never reads `.reviewers`/`.author` (the only
    # fields entities affect) — only `.product` (reinterpreted as the title below),
    # `.task_url`, `.docs_url`, `.merge_requests`.
    parsed = parse_post(command.args)

    try:
        draft = await announcements.create_draft(
            session,
            composer_user_id=user.id,
            composer_username=user.username,
            chat_id=message.chat.id,
            parsed=parsed,
            settings=settings,
        )
    except announcements.NoMergeRequestFound:
        await message.answer(texts.t(locale, "announce_no_mr"))
        return
    except announcements.ProjectNotConfigured as exc:
        await message.answer(
            texts.t(locale, "announce_project_unconfigured", project=exc.project_path)
        )
        return
    except announcements.ConflictingProjectConfigs as exc:
        await message.answer(
            texts.t(
                locale,
                "announce_conflicting_projects",
                base=exc.base_project,
                others=", ".join(exc.conflicting_projects),
            )
        )
        return

    text, markup = announcement.render_preview(
        draft, composer_locale=locale, channel_locale=settings.default_locale
    )
    sent = await message.answer(text, reply_markup=markup, disable_web_page_preview=True)
    await repo.set_draft_preview_message(session, draft, sent.message_id)


@router.message(F.text.in_(MENU_ANNOUNCE_TEXTS))
async def on_announce_button(message: Message, session: AsyncSession, settings: Settings) -> None:
    """The "Announce" menu button can't carry a title/MR — Telegram sends back only
    the button's own label as plain text - so this can only ever land on the same
    usage hint as a bare `/announce`, same as `on_announce`'s no-args branch."""
    user = message.from_user
    user_row = await repo.upsert_user(
        session, user.id, user.username, user.full_name, user.language_code
    )
    locale = resolve_locale(user_row.locale, user.language_code, default=settings.default_locale)
    if not user.username:
        await message.answer(texts.t(locale, "announce_no_username"))
        return
    await message.answer(texts.t(locale, "announce_usage"))


@router.callback_query(AnnounceAction.filter())
async def on_announce_action(
    query: CallbackQuery,
    callback_data: AnnounceAction,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    user = query.from_user
    user_row = await repo.upsert_user(
        session, user.id, user.username, user.full_name, user.language_code
    )
    locale = resolve_locale(user_row.locale, user.language_code, default=settings.default_locale)

    draft = await repo.get_draft(session, callback_data.draft_id)
    if draft is None or draft.published_at is not None or draft.cancelled_at is not None:
        await query.answer(texts.t(locale, "announce_draft_gone"), show_alert=True)
        return
    if draft.composer_user_id != user.id:
        await query.answer(texts.t(locale, "announce_not_yours"), show_alert=True)
        return

    handler = {"publish": _publish, "reroll": _reroll, "cancel": _cancel}[callback_data.action]
    answer = await handler(session, draft, bot, settings, locale)
    await query.answer(answer, show_alert=False)


async def _publish(
    session: AsyncSession, draft: AnnouncementDraft, bot: Bot, settings: Settings, locale: str
) -> str:
    try:
        await announcements.publish(bot, session, draft, settings)
    except announcements.ChannelNotConfigured:
        return texts.t(locale, "announce_channel_unconfigured")
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("could not publish announcement draft %s: %s", draft.id, exc)
        return texts.t(locale, "announce_publish_failed")

    await _safe_edit(bot, draft, texts.t(locale, "announce_published_body"))
    return texts.t(locale, "announce_published")


async def _reroll(
    session: AsyncSession, draft: AnnouncementDraft, bot: Bot, settings: Settings, locale: str
) -> str:
    draft = await announcements.reroll(session, draft, settings)
    text, markup = announcement.render_preview(
        draft, composer_locale=locale, channel_locale=settings.default_locale
    )
    await _safe_edit(bot, draft, text, markup)
    return texts.t(locale, "announce_rerolled")


async def _cancel(
    session: AsyncSession, draft: AnnouncementDraft, bot: Bot, settings: Settings, locale: str
) -> str:
    await repo.mark_draft_cancelled(session, draft)
    await _safe_edit(bot, draft, texts.t(locale, "announce_cancelled"))
    return texts.t(locale, "announce_cancelled")


async def _safe_edit(bot: Bot, draft: AnnouncementDraft, text: str, markup=None) -> None:
    """Edit the composer's own preview message — legal, the bot authored it.

    Same "no-op edits are fine" tolerance as `card.refresh`: whichever action races
    to update the message first wins, and Telegram rejecting an identical edit is not
    worth surfacing as a real failure.
    """
    try:
        await bot.edit_message_text(
            chat_id=draft.chat_id,
            message_id=draft.preview_message_id,
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            logger.warning("could not update announce preview %s: %s", draft.id, exc)
