"""Data access. Query shapes the rest of the bot needs, and nothing else."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..domain.state import NUDGEABLE, Assignment, Event, ReviewerState
from ..i18n import normalize_locale
from ..parsing.gitlab_url import MergeRequestRef
from .models import (
    AnnouncementDraft,
    AssignmentTransition,
    BotAdmin,
    ChannelAttachment,
    MergeRequestLink,
    NudgeLog,
    Review,
    ReviewerAssignment,
    ReviewerPoolMember,
    User,
    utcnow,
)

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


async def get_unique_user_by_full_name(session: AsyncSession, full_name: str) -> User | None:
    """Fallback for an "Автор:"/"Ревьювер:" line that names someone by their Telegram
    display name instead of an @handle — common since typing a bare name is the path
    of least resistance. Only resolves when exactly one known user has that name
    (case-insensitively): a shared first name (two people named "Alice") must not
    risk sending someone else's notifications to the wrong person, so it backs off to
    "unresolved" instead of guessing, same as if the name had never matched at all.
    """
    result = await session.execute(select(User).where(User.full_name.ilike(full_name)))
    rows = result.scalars().all()
    return rows[0] if len(rows) == 1 else None


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


async def closed_reviews_pending_channel_cleanup(
    session: AsyncSession, cutoff: datetime
) -> list[Review]:
    """Closed reviews whose channel post hasn't been removed yet and have sat past
    `cutoff` (`closed_at + Settings.channel_cleanup_delay`) — `channel_cleanup_tick`'s
    work list. Also how a review closed before this feature existed (`closed_at` far
    in the past, `channel_post_deleted_at` still `None`) gets swept up on the very
    first tick after deploy, with no separate backfill needed.

    Excludes anything already given up on (`channel_cleanup_abandoned_at` set) — a
    post past Telegram's 48-hour bot-deletion window stays that way forever, so
    retrying it on every future tick would just be a no-op warning log, repeated.
    """
    result = await session.execute(
        select(Review).where(
            Review.is_closed.is_(True),
            Review.channel_post_deleted_at.is_(None),
            Review.channel_cleanup_abandoned_at.is_(None),
            Review.closed_at.is_not(None),
            Review.closed_at <= cutoff,
        )
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


# --- assignment transitions (stats) -----------------------------------------


async def record_transition(
    session: AsyncSession,
    assignment: ReviewerAssignment,
    *,
    from_state: ReviewerState,
    to_state: ReviewerState,
    event: Event,
    at: datetime,
) -> None:
    """Append-only history entry — see `AssignmentTransition`'s docstring for why
    `ReviewerAssignment`'s own columns can't answer "how long was it in state X"."""
    session.add(
        AssignmentTransition(
            assignment_id=assignment.id,
            review_id=assignment.review_id,
            from_state=from_state,
            to_state=to_state,
            event=event.value,
            at=at,
        )
    )


async def transitions_between(
    session: AsyncSession, since: datetime, until: datetime
) -> list[AssignmentTransition]:
    """Ordered per-assignment so `services.stats` can pair consecutive rows directly."""
    result = await session.execute(
        select(AssignmentTransition)
        .where(AssignmentTransition.at >= since, AssignmentTransition.at < until)
        .order_by(AssignmentTransition.assignment_id, AssignmentTransition.at)
    )
    return list(result.scalars().all())


# --- bot admins ---------------------------------------------------------------


async def list_admins(session: AsyncSession) -> list[BotAdmin]:
    result = await session.execute(select(BotAdmin).order_by(BotAdmin.created_at))
    return list(result.scalars().all())


async def is_db_admin(session: AsyncSession, telegram_user_id: int) -> bool:
    result = await session.execute(
        select(BotAdmin.id).where(BotAdmin.telegram_user_id == telegram_user_id)
    )
    return result.scalar_one_or_none() is not None


async def add_admin(
    session: AsyncSession, *, telegram_user_id: int, username: str | None, added_by_user_id: int
) -> BotAdmin | None:
    """`None` if already an admin — the caller treats that as a no-op, not an error."""
    if await is_db_admin(session, telegram_user_id):
        return None
    admin = BotAdmin(
        telegram_user_id=telegram_user_id, username=username, added_by_user_id=added_by_user_id
    )
    session.add(admin)
    await session.flush()
    return admin


async def remove_admin(session: AsyncSession, telegram_user_id: int) -> bool:
    result = await session.execute(
        select(BotAdmin).where(BotAdmin.telegram_user_id == telegram_user_id)
    )
    admin = result.scalar_one_or_none()
    if admin is None:
        return False
    await session.delete(admin)
    await session.flush()
    return True


# --- reviewer pool --------------------------------------------------------------


async def list_pool_members(session: AsyncSession) -> list[ReviewerPoolMember]:
    result = await session.execute(
        select(ReviewerPoolMember).order_by(ReviewerPoolMember.created_at)
    )
    return list(result.scalars().all())


async def get_pool_member(session: AsyncSession, username: str) -> ReviewerPoolMember | None:
    result = await session.execute(
        select(ReviewerPoolMember).where(ReviewerPoolMember.username.ilike(username))
    )
    return result.scalars().first()


async def add_pool_member(
    session: AsyncSession, *, username: str, added_by_user_id: int
) -> ReviewerPoolMember | None:
    """`None` if already in the pool (case-insensitively) — a no-op, not an error."""
    if await get_pool_member(session, username) is not None:
        return None
    member = ReviewerPoolMember(username=username, added_by_user_id=added_by_user_id)
    session.add(member)
    await session.flush()
    return member


async def remove_pool_member(session: AsyncSession, username: str) -> bool:
    member = await get_pool_member(session, username)
    if member is None:
        return False
    await session.delete(member)
    await session.flush()
    return True


# --- announcement drafts ------------------------------------------------------


async def create_draft(
    session: AsyncSession,
    *,
    composer_user_id: int,
    composer_username: str,
    chat_id: int,
    project_path: str,
    product: str,
    title: str | None,
    task_url: str | None,
    docs_url: str | None,
    description: str | None = None,
    merge_requests: list[MergeRequestRef],
    techlead_username: str | None,
    pool_pick_usernames: list[str],
    attachments: list[dict] | None = None,
) -> AnnouncementDraft:
    draft = AnnouncementDraft(
        composer_user_id=composer_user_id,
        composer_username=composer_username,
        chat_id=chat_id,
        project_path=project_path,
        product=product,
        title=title,
        task_url=task_url,
        docs_url=docs_url,
        description=description,
        merge_requests_json=json.dumps([ref.model_dump() for ref in merge_requests]),
        techlead_username=techlead_username,
        pool_pick_usernames_json=json.dumps(pool_pick_usernames),
        attachments_json=json.dumps(attachments or []),
    )
    session.add(draft)
    await session.flush()
    return draft


async def get_draft(session: AsyncSession, draft_id: int) -> AnnouncementDraft | None:
    return await session.get(AnnouncementDraft, draft_id)


async def set_draft_preview_message(
    session: AsyncSession, draft: AnnouncementDraft, message_id: int
) -> None:
    draft.preview_message_id = message_id
    await session.flush()


async def set_draft_pool_picks(
    session: AsyncSession, draft: AnnouncementDraft, usernames: list[str]
) -> None:
    draft.pool_pick_usernames_json = json.dumps(usernames)
    await session.flush()


async def mark_draft_published(session: AsyncSession, draft: AnnouncementDraft) -> None:
    draft.published_at = utcnow()
    await session.flush()


async def mark_draft_cancelled(session: AsyncSession, draft: AnnouncementDraft) -> None:
    draft.cancelled_at = utcnow()
    await session.flush()


def draft_merge_requests(draft: AnnouncementDraft) -> list[MergeRequestRef]:
    return [MergeRequestRef(**item) for item in json.loads(draft.merge_requests_json)]


def draft_pool_picks(draft: AnnouncementDraft) -> list[str]:
    return list(json.loads(draft.pool_pick_usernames_json))


async def add_draft_attachment(
    session: AsyncSession, draft: AnnouncementDraft, *, file_id: str, kind: str
) -> int:
    """Appends one file as it arrives — the composer sends them one message at a
    time, not as a single batch — and returns the new total, for the "added N" ack.
    """
    attachments = draft_attachments(draft)
    attachments.append({"file_id": file_id, "kind": kind})
    draft.attachments_json = json.dumps(attachments)
    await session.flush()
    return len(attachments)


def draft_attachments(draft: AnnouncementDraft) -> list[dict]:
    return list(json.loads(draft.attachments_json))


# --- channel attachments (files sent alongside a published post) ----------------


async def record_channel_attachment(
    session: AsyncSession, *, channel_chat_id: int, post_message_id: int, message_id: int
) -> None:
    session.add(
        ChannelAttachment(
            channel_chat_id=channel_chat_id,
            post_message_id=post_message_id,
            message_id=message_id,
        )
    )
    await session.flush()


async def channel_attachment_message_ids(
    session: AsyncSession, channel_chat_id: int, post_message_id: int
) -> list[int]:
    result = await session.execute(
        select(ChannelAttachment.message_id).where(
            ChannelAttachment.channel_chat_id == channel_chat_id,
            ChannelAttachment.post_message_id == post_message_id,
        )
    )
    return list(result.scalars().all())


async def clear_channel_attachments(
    session: AsyncSession, channel_chat_id: int, post_message_id: int
) -> None:
    result = await session.execute(
        select(ChannelAttachment).where(
            ChannelAttachment.channel_chat_id == channel_chat_id,
            ChannelAttachment.post_message_id == post_message_id,
        )
    )
    for row in result.scalars().all():
        await session.delete(row)
    await session.flush()


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
