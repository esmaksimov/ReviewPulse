"""Telegram implementation of the NudgeSender protocol."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repo
from ..db.models import ReviewerAssignment
from ..domain.escalation import Nudge
from . import card, keyboards, texts

logger = logging.getLogger(__name__)


class TelegramNudgeSender(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    bot: Bot
    session: AsyncSession

    async def send(self, assignment: ReviewerAssignment, nudge: Nudge) -> bool:
        review = assignment.review
        url = card.review_url(review)
        headline = " — ".join(part for part in (review.product, review.title) if part) or "Ревью"

        try:
            await self.bot.send_message(
                chat_id=assignment.telegram_user_id,
                text=texts.nudge(
                    reason=nudge.reason,
                    headline=headline,
                    overdue_by=nudge.overdue_by,
                    review_url=url,
                    merge_request_urls=[link.web_url for link in review.merge_requests],
                ),
                reply_markup=keyboards.nudge_actions(assignment.id, url),
                disable_web_page_preview=True,
            )
        except TelegramForbiddenError:
            # Blocked, or never started the bot. Stop trying until they come back:
            # /start flips this flag again.
            logger.info("user %s cannot be DMed; muting", assignment.telegram_user_id)
            user = await repo.get_user_by_telegram_id(self.session, assignment.telegram_user_id)
            if user is not None:
                user.can_be_dmed = False
            return False
        except TelegramRetryAfter as exc:
            logger.warning("flood limit, skipping this tick: retry after %ss", exc.retry_after)
            return False

        return True
