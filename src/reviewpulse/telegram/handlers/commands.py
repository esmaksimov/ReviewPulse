"""Private-chat commands.

/start matters more than it looks: Telegram forbids a bot from writing first, so a
reviewer who never starts the bot can never be reminded. This is where a @handle from
a post finally gets matched to a numeric id.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...db import repo
from ...db.models import utcnow
from ...domain.escalation import policy_from_settings
from ...domain.workhours import calendar_from_settings
from ...i18n import SUPPORTED_LOCALES, normalize_locale, resolve_locale
from ...services import reviews as review_service
from .. import card, texts

logger = logging.getLogger(__name__)

router = Router(name="commands")
router.message.filter(F.chat.type == "private")

_DURATION = re.compile(r"^\s*(\d+)\s*([hdчд])?\s*$", re.IGNORECASE)


async def _locale_for(session: AsyncSession, message: Message, settings: Settings) -> str:
    user_row = await repo.get_user_by_telegram_id(session, message.from_user.id)
    return resolve_locale(
        user_row.locale if user_row else None,
        message.from_user.language_code,
        default=settings.default_locale,
    )


@router.message(CommandStart())
async def on_start(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = message.from_user
    user_row = await repo.upsert_user(
        session, user.id, user.username, user.full_name, user.language_code
    )
    locale = resolve_locale(user_row.locale, user.language_code, default=settings.default_locale)

    if not user.username:
        await message.answer(texts.t(locale, "no_username"))
        return

    linked = await review_service.link_user_to_assignments(session, user.id, user.username)
    reply = texts.t(locale, "start_message")
    if linked:
        reply += texts.t(locale, "start_found_open", count=linked)
    await message.answer(reply)


@router.message(Command("status"))
async def on_status(message: Message, session: AsyncSession, settings: Settings) -> None:
    locale = await _locale_for(session, message, settings)
    assignments = await repo.assignments_for_user(session, message.from_user.id)
    if not assignments:
        await message.answer(texts.t(locale, "nothing_pending"))
        return

    policy = policy_from_settings(settings, calendar_from_settings(settings))
    lines = [texts.t(locale, "status_header"), ""]
    for row in assignments:
        lines.append(
            texts.status_line(
                locale,
                card.headline(row.review, locale),
                row.state,
                policy.deadline_for(repo.to_domain(row)),
                settings.timezone_offset_hours,
            )
        )
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@router.message(Command("link"))
async def on_link(
    message: Message, command: CommandObject, session: AsyncSession, settings: Settings
) -> None:
    """Map this Telegram user to a GitLab login, so their threads can be attributed."""
    locale = await _locale_for(session, message, settings)
    if not command.args:
        await message.answer(texts.t(locale, "link_usage"))
        return

    user = await repo.upsert_user(
        session,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
        message.from_user.language_code,
    )
    user.gitlab_username = command.args.strip().lstrip("@")
    await message.answer(texts.t(locale, "link_done", login=user.gitlab_username))


@router.message(Command("lang"))
async def on_lang(
    message: Message, command: CommandObject, session: AsyncSession, settings: Settings
) -> None:
    """Explicit language choice — takes priority over Telegram's own language_code."""
    locale = await _locale_for(session, message, settings)
    requested = normalize_locale(command.args) if command.args else None
    if requested is None:
        names = ", ".join(f"{code} ({texts.LANGUAGE_NAMES[code]})" for code in SUPPORTED_LOCALES)
        await message.answer(texts.t(locale, "lang_usage", list=names))
        return

    user = await repo.upsert_user(
        session,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
        message.from_user.language_code,
    )
    user.locale = requested
    await message.answer(texts.t(requested, "lang_done", name=texts.LANGUAGE_NAMES[requested]))


@router.message(Command("mute"))
async def on_mute(
    message: Message, command: CommandObject, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, message, settings)
    duration = _parse_duration(command.args or "8h")
    if duration is None:
        await message.answer(texts.t(locale, "mute_usage"))
        return

    user = await repo.upsert_user(session, message.from_user.id, message.from_user.username)
    user.muted_until = utcnow() + duration
    await message.answer(texts.t(locale, "mute_done", duration=texts.humanize(locale, duration)))


@router.message(Command("unmute"))
async def on_unmute(message: Message, session: AsyncSession, settings: Settings) -> None:
    locale = await _locale_for(session, message, settings)
    user = await repo.upsert_user(session, message.from_user.id, message.from_user.username)
    user.muted_until = None
    await message.answer(texts.t(locale, "unmute_done"))


def _parse_duration(raw: str) -> timedelta | None:
    match = _DURATION.match(raw)
    if not match:
        return None
    amount = int(match.group(1))
    unit = (match.group(2) or "h").lower()
    return timedelta(days=amount) if unit in {"d", "д"} else timedelta(hours=amount)
