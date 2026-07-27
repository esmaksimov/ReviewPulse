"""Bot and dispatcher wiring."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from ..config import Settings
from ..db.session import Database
from .handlers import build_router
from .middleware import DependenciesMiddleware

logger = logging.getLogger(__name__)

#: Reactions are deliberately absent: in a channel they are anonymous, so they cannot
#: tell us *who* approved. The card's inline buttons are the source of truth instead.
ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
    "my_chat_member",
]


def build_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher(database: Database, settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher()
    middleware = DependenciesMiddleware(database, settings)
    dispatcher.message.middleware(middleware)
    dispatcher.edited_message.middleware(middleware)
    dispatcher.channel_post.middleware(middleware)
    dispatcher.edited_channel_post.middleware(middleware)
    dispatcher.callback_query.middleware(middleware)
    dispatcher.include_router(build_router())
    return dispatcher
