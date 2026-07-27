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
from ...services import reviews as review_service
from .. import texts

logger = logging.getLogger(__name__)

router = Router(name="commands")
router.message.filter(F.chat.type == "private")

_DURATION = re.compile(r"^\s*(\d+)\s*([hdчд])?\s*$", re.IGNORECASE)


@router.message(CommandStart())
async def on_start(message: Message, session: AsyncSession) -> None:
    user = message.from_user
    await repo.upsert_user(session, user.id, user.username, user.full_name)

    if not user.username:
        await message.answer(texts.NO_USERNAME)
        return

    linked = await review_service.link_user_to_assignments(session, user.id, user.username)
    reply = texts.START
    if linked:
        reply += f"\n\nНашёл открытых ревью на тебе: {linked}. Посмотреть — /status"
    await message.answer(reply)


@router.message(Command("status"))
async def on_status(message: Message, session: AsyncSession, settings: Settings) -> None:
    assignments = await repo.assignments_for_user(session, message.from_user.id)
    if not assignments:
        await message.answer(texts.NOTHING_PENDING)
        return

    policy = policy_from_settings(settings, calendar_from_settings(settings))
    lines = ["<b>Ревью на тебе</b>", ""]
    for row in assignments:
        headline = " — ".join(part for part in (row.review.product, row.review.title) if part)
        lines.append(
            texts.status_line(
                headline or "Ревью",
                row.state,
                policy.deadline_for(repo.to_domain(row)),
                settings.timezone_offset_hours,
            )
        )
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@router.message(Command("link"))
async def on_link(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    """Map this Telegram user to a GitLab login, so their threads can be attributed."""
    if not command.args:
        await message.answer("Формат: <code>/link ivanov</code>")
        return

    user = await repo.upsert_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    user.gitlab_username = command.args.strip().lstrip("@")
    await message.answer(f"Связал с GitLab: <code>{user.gitlab_username}</code>")


@router.message(Command("mute"))
async def on_mute(message: Message, command: CommandObject, session: AsyncSession) -> None:
    duration = _parse_duration(command.args or "8h")
    if duration is None:
        await message.answer("Формат: <code>/mute 2h</code> или <code>/mute 1d</code>")
        return

    user = await repo.upsert_user(session, message.from_user.id, message.from_user.username)
    user.muted_until = utcnow() + duration
    await message.answer(f"Молчу {texts.humanize(duration)}. Вернуть — /unmute")


@router.message(Command("unmute"))
async def on_unmute(message: Message, session: AsyncSession) -> None:
    user = await repo.upsert_user(session, message.from_user.id, message.from_user.username)
    user.muted_until = None
    await message.answer("Снова напоминаю.")


def _parse_duration(raw: str) -> timedelta | None:
    match = _DURATION.match(raw)
    if not match:
        return None
    amount = int(match.group(1))
    unit = (match.group(2) or "h").lower()
    return timedelta(days=amount) if unit in {"d", "д"} else timedelta(hours=amount)
