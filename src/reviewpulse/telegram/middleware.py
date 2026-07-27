"""Give every handler a database session and the settings, and commit on success."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from ..config import Settings
from ..db.session import Database


class DependenciesMiddleware(BaseMiddleware):
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["settings"] = self.settings
        async with self.database.session() as session:
            data["session"] = session
            return await handler(event, data)
