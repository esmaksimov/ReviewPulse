"""Telegram implementation of the NudgeSender protocol."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repo
from ..db.models import Review, ReviewerAssignment
from ..domain.escalation import Nudge
from ..i18n import resolve_locale
from . import card, keyboards, texts

logger = logging.getLogger(__name__)


class TelegramNudgeSender(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    bot: Bot
    session: AsyncSession
    default_locale: str = "en"

    async def send(self, assignment: ReviewerAssignment, nudge: Nudge) -> bool:
        review = assignment.review
        url = card.review_url(review)
        user = await repo.get_user_by_telegram_id(self.session, assignment.telegram_user_id)
        locale = resolve_locale(user.locale if user else None, default=self.default_locale)

        try:
            await self.bot.send_message(
                chat_id=assignment.telegram_user_id,
                text=texts.nudge(
                    locale,
                    reason=nudge.reason,
                    headline=card.headline(review, locale),
                    overdue_by=nudge.overdue_by,
                    review_url=url,
                    merge_request_urls=[link.web_url for link in review.merge_requests],
                ),
                reply_markup=keyboards.nudge_actions(locale, assignment.id, url),
                disable_web_page_preview=True,
            )
        except TelegramForbiddenError:
            # Blocked, or never started the bot. Stop trying until they come back:
            # /start flips this flag again.
            logger.info("user %s cannot be DMed; muting", assignment.telegram_user_id)
            if user is not None:
                user.can_be_dmed = False
            return False
        except TelegramRetryAfter as exc:
            logger.warning("flood limit, skipping this tick: retry after %ss", exc.retry_after)
            return False

        return True


async def notify_author_changes_requested(
    bot: Bot,
    session: AsyncSession,
    review: Review,
    reviewer_label: str,
    default_locale: str,
) -> None:
    """Tell the author a reviewer just put the ball back on them.

    A no-op unless the post named its author on an "Автор:" line — see
    `services.reviews._sync_author` — since a channel post itself carries no
    `from_user` to identify the author from automatically. Called from both the
    card-button path (`handlers.callbacks`) and the GitLab-sync path
    (`scheduler.jobs.gitlab_tick`), so it means the same thing either way a reviewer's
    request lands.
    """
    if review.author_user_id is None:
        return

    author = await repo.get_user_by_telegram_id(session, review.author_user_id)
    if author is not None and not author.can_be_dmed:
        return
    locale = resolve_locale(author.locale if author else None, default=default_locale)

    try:
        await bot.send_message(
            chat_id=review.author_user_id,
            text=texts.author_changes_requested(
                locale,
                reviewer=reviewer_label,
                headline=card.headline(review, locale),
                review_url=card.review_url(review),
                merge_request_urls=[link.web_url for link in review.merge_requests],
            ),
            disable_web_page_preview=True,
        )
    except TelegramForbiddenError:
        logger.info("author %s cannot be DMed; muting", review.author_user_id)
        if author is not None:
            author.can_be_dmed = False
    except TelegramRetryAfter as exc:
        logger.warning("flood limit, dropping author notice: retry after %ss", exc.retry_after)


async def notify_author_review_approved(
    bot: Bot,
    session: AsyncSession,
    review: Review,
    default_locale: str,
) -> None:
    """Tell the author their review just reached quorum and closed.

    Mirrors `notify_author_changes_requested` — same author-resolution gate, same
    `can_be_dmed` mute-on-block behaviour. Only ever fired from the button-press path
    (`handlers.callbacks`): GitLab sync never issues an APPROVE event on its own (see
    `services.gitlab_sync._SYNCABLE`), so a review can only close there.
    """
    if review.author_user_id is None:
        return

    author = await repo.get_user_by_telegram_id(session, review.author_user_id)
    if author is not None and not author.can_be_dmed:
        return
    locale = resolve_locale(author.locale if author else None, default=default_locale)

    try:
        await bot.send_message(
            chat_id=review.author_user_id,
            text=texts.author_review_approved(
                locale,
                headline=card.headline(review, locale),
                review_url=card.review_url(review),
                merge_request_urls=[link.web_url for link in review.merge_requests],
            ),
            disable_web_page_preview=True,
        )
    except TelegramForbiddenError:
        logger.info("author %s cannot be DMed; muting", review.author_user_id)
        if author is not None:
            author.can_be_dmed = False
    except TelegramRetryAfter as exc:
        logger.warning("flood limit, dropping approval notice: retry after %ss", exc.retry_after)


async def notify_reviewer_fixes_done(
    bot: Bot,
    session: AsyncSession,
    review: Review,
    assignment: ReviewerAssignment,
    default_locale: str,
) -> None:
    """Tell a reviewer the author just marked their requested changes as done —
    they're back on the hook for another look.

    Without this, a reviewer moved to AWAITING_RECHECK finds out only once the SLA
    nudge fires (`scheduler.jobs.nudge_tick`'s `nudge_stale`), which can be hours
    later. Called once per moved assignment from both the button path
    (`handlers.callbacks._fixes_done`) and the GitLab-sync path
    (`scheduler.jobs.gitlab_tick`, when GitLab reports every thread resolved).
    """
    if assignment.telegram_user_id is None:
        return

    reviewer = await repo.get_user_by_telegram_id(session, assignment.telegram_user_id)
    if reviewer is not None and not reviewer.can_be_dmed:
        return
    locale = resolve_locale(reviewer.locale if reviewer else None, default=default_locale)

    try:
        await bot.send_message(
            chat_id=assignment.telegram_user_id,
            text=texts.reviewer_fixes_done(
                locale,
                headline=card.headline(review, locale),
                review_url=card.review_url(review),
                merge_request_urls=[link.web_url for link in review.merge_requests],
            ),
            disable_web_page_preview=True,
        )
    except TelegramForbiddenError:
        logger.info("reviewer %s cannot be DMed; muting", assignment.telegram_user_id)
        if reviewer is not None:
            reviewer.can_be_dmed = False
    except TelegramRetryAfter as exc:
        logger.warning("flood limit, dropping fixes-done notice: retry after %ss", exc.retry_after)
