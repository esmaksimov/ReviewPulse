"""Data access. Query shapes the rest of the bot needs, and nothing else."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..domain.state import NUDGEABLE, Assignment, ReviewerState
from ..i18n import normalize_locale
from .models import MergeRequestLink, NudgeLog, Review, ReviewerAssignment, User

# --- users ------------------------------------------------------------------


async def get_user_by_telegram_id(session: AsyncSession, telegram_user_id: int) -> User | None:
    result = await session.execute(
        select(User).where(User.telegram_user_id == telegram_user_id)
    )
    return result.scalar_one_or_none()


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(
        select(User).where(User.username.ilike(username))
    )
    return result.scalars().first()


async def upsert_user(
    session: AsyncSession,
    telegram_user_id: int,
    username: str | None = None,
    full_name: str | None = None,
    language_code: str | None = None,
) -> User:
    """Record (or refresh) a user we can DM. Called from /start and every button press.

    `language_code` seeds `User.locale` from Telegram's own client-language guess, but
    only on first contact — it must never overwrite a locale the user chose with /lang.
    """
    user = await get_user_by_telegram_id(session, telegram_user_id)
    if user is None:
        user = User(telegram_user_id=telegram_user_id, locale=normalize_locale(language_code))
        session.add(user)
    if username is not None:
        user.username = username
    if full_name is not None:
        user.full_name = full_name
    user.can_be_dmed = True
    await session.flush()
    return user


# --- reviews ----------------------------------------------------------------


async def get_review_by_channel_message(
    session: AsyncSession, channel_chat_id: int, channel_message_id: int
) -> Review | None:
    result = await session.execute(
        select(Review)
        .where(
            Review.channel_chat_id == channel_chat_id,
            Review.channel_message_id == channel_message_id,
        )
        .options(selectinload(Review.assignments), selectinload(Review.merge_requests))
    )
    return result.scalar_one_or_none()


async def get_review(session: AsyncSession, review_id: int) -> Review | None:
    result = await session.execute(
        select(Review)
        .where(Review.id == review_id)
        .options(selectinload(Review.assignments), selectinload(Review.merge_requests))
    )
    return result.scalar_one_or_none()


async def open_reviews(session: AsyncSession) -> list[Review]:
    result = await session.execute(
        select(Review)
        .where(Review.is_closed.is_(False))
        .options(selectinload(Review.assignments), selectinload(Review.merge_requests))
        .order_by(Review.posted_at)
    )
    return list(result.scalars().all())


async def open_reviews_with_merge_requests(session: AsyncSession) -> list[Review]:
    """Open reviews that actually have MRs to poll — the GitLab sync's work list."""
    result = await session.execute(
        select(Review)
        .join(MergeRequestLink)
        .where(Review.is_closed.is_(False))
        .options(selectinload(Review.assignments), selectinload(Review.merge_requests))
        .distinct()
        .order_by(Review.posted_at)
    )
    return list(result.scalars().all())


# --- assignments ------------------------------------------------------------


async def nudgeable_assignments(
    session: AsyncSession, now: datetime
) -> list[ReviewerAssignment]:
    """Assignments where the ball is on a reachable, un-muted reviewer.

    Users who blocked the bot are excluded at the query level: without that, a failed
    send leaves `last_nudge_at` untouched and the tick would retry them every minute.
    """
    muted_or_unreachable = (
        select(User.telegram_user_id)
        .where(
            User.telegram_user_id == ReviewerAssignment.telegram_user_id,
            or_(User.can_be_dmed.is_(False), User.muted_until > now),
        )
        .exists()
    )

    result = await session.execute(
        select(ReviewerAssignment)
        .join(Review)
        .where(
            Review.is_closed.is_(False),
            ReviewerAssignment.state.in_(list(NUDGEABLE)),
            ReviewerAssignment.telegram_user_id.is_not(None),
            ReviewerAssignment.removed_at.is_(None),
            ~muted_or_unreachable,
        )
        .options(selectinload(ReviewerAssignment.review).selectinload(Review.merge_requests))
        .order_by(ReviewerAssignment.ball_since)
    )
    return list(result.scalars().all())


async def assignments_for_user(
    session: AsyncSession, telegram_user_id: int, include_closed: bool = False
) -> list[ReviewerAssignment]:
    query = (
        select(ReviewerAssignment)
        .join(Review)
        .where(
            ReviewerAssignment.telegram_user_id == telegram_user_id,
            ReviewerAssignment.removed_at.is_(None),
        )
        .options(selectinload(ReviewerAssignment.review).selectinload(Review.merge_requests))
        .order_by(ReviewerAssignment.ball_since)
    )
    if not include_closed:
        query = query.where(
            Review.is_closed.is_(False),
            ReviewerAssignment.state != ReviewerState.APPROVED,
        )
    result = await session.execute(query)
    return list(result.scalars().all())


async def unlinked_assignments_for_username(
    session: AsyncSession, username: str
) -> list[ReviewerAssignment]:
    """Assignments named by @handle that still lack a telegram id — resolved on /start."""
    result = await session.execute(
        select(ReviewerAssignment)
        .join(Review)
        .where(
            ReviewerAssignment.telegram_user_id.is_(None),
            ReviewerAssignment.username.ilike(username),
            ReviewerAssignment.removed_at.is_(None),
            Review.is_closed.is_(False),
        )
        .options(selectinload(ReviewerAssignment.review))
    )
    return list(result.scalars().all())


async def unlinked_authored_reviews_for_username(
    session: AsyncSession, username: str
) -> list[Review]:
    """Reviews whose "Автор:" line named this handle before it resolved to an id —
    backfilled the same way `unlinked_assignments_for_username` is, on /start."""
    result = await session.execute(
        select(Review).where(
            Review.author_user_id.is_(None),
            Review.author_username.ilike(username),
            Review.is_closed.is_(False),
        )
    )
    return list(result.scalars().all())


async def find_assignment(
    session: AsyncSession, review_id: int, telegram_user_id: int
) -> ReviewerAssignment | None:
    result = await session.execute(
        select(ReviewerAssignment)
        .where(
            ReviewerAssignment.review_id == review_id,
            ReviewerAssignment.telegram_user_id == telegram_user_id,
            ReviewerAssignment.removed_at.is_(None),
        )
        .options(selectinload(ReviewerAssignment.review))
    )
    return result.scalar_one_or_none()


async def reviews_awaiting_author(
    session: AsyncSession, telegram_user_id: int
) -> list[Review]:
    """Open reviews this person authored where a reviewer is waiting on their fixes.

    Requires `Review.author_user_id` to already be linked — see
    `services.reviews._sync_author` — which only happens for a post that named its
    author on an "Автор:" line, since a channel post carries no `from_user` to infer
    it from.
    """
    result = await session.execute(
        select(Review)
        .join(ReviewerAssignment)
        .where(
            Review.author_user_id == telegram_user_id,
            Review.is_closed.is_(False),
            ReviewerAssignment.state == ReviewerState.CHANGES_REQUESTED,
            ReviewerAssignment.removed_at.is_(None),
        )
        .options(selectinload(Review.assignments), selectinload(Review.merge_requests))
        .distinct()
        .order_by(Review.posted_at)
    )
    return list(result.scalars().all())


# --- domain <-> row conversion ---------------------------------------------


def to_domain(row: ReviewerAssignment) -> Assignment:
    return Assignment(
        state=row.state,
        ball_since=row.ball_since,
        last_nudge_at=row.last_nudge_at,
        nudges_today=row.nudges_today,
        snoozed_until=row.snoozed_until,
    )


def apply_domain(row: ReviewerAssignment, assignment: Assignment) -> None:
    row.state = assignment.state
    row.ball_since = assignment.ball_since
    row.last_nudge_at = assignment.last_nudge_at
    row.nudges_today = assignment.nudges_today
    row.snoozed_until = assignment.snoozed_until


# --- nudge log --------------------------------------------------------------


async def record_nudge(
    session: AsyncSession,
    assignment: ReviewerAssignment,
    reason: str,
    sent_at: datetime,
    same_day: bool,
    delivered: bool = True,
    error: str | None = None,
) -> None:
    if delivered:
        assignment.nudges_today = (assignment.nudges_today if same_day else 0) + 1
        assignment.last_nudge_at = sent_at
    session.add(
        NudgeLog(
            assignment_id=assignment.id,
            reason=reason,
            sent_at=sent_at,
            delivered=delivered,
            error=error,
        )
    )
