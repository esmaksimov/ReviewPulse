"""Review lifecycle: create from a post, apply reviewer verdicts, close.

This is where the pure domain meets the database. Telegram handlers and the GitLab
sync both come through here, so a verdict means the same thing whichever way it arrived.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repo
from ..db.models import MergeRequestLink, Review, ReviewerAssignment, utcnow
from ..domain.state import Event, IllegalTransition, ReviewerState
from ..parsing.post_parser import ParsedPost

logger = logging.getLogger(__name__)


class VerdictResult(BaseModel):
    # Carries an ORM row, which pydantic must accept as-is rather than validate.
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    assignment: ReviewerAssignment
    changed: bool
    """False when the event was a no-op (same button pressed twice, repeated poll)."""

    review_closed: bool = False


async def create_or_update_review(
    session: AsyncSession,
    *,
    channel_chat_id: int,
    channel_message_id: int,
    post: ParsedPost,
    raw_text: str,
    posted_at: datetime,
    author_user_id: int | None = None,
    author_label: str | None = None,
) -> Review:
    """Idempotent upsert keyed on the channel message.

    The channel post and its auto-forwarded copy in the discussion group arrive as two
    independent updates in an unpredictable order, and an edited post replays the first
    one — so this must be safe to call repeatedly.
    """
    review = await repo.get_review_by_channel_message(session, channel_chat_id, channel_message_id)
    if review is None:
        # The collections are passed explicitly so they count as loaded: touching an
        # unloaded collection on a freshly flushed row would emit a lazy load, which
        # under asyncio raises MissingGreenlet.
        review = Review(
            channel_chat_id=channel_chat_id,
            channel_message_id=channel_message_id,
            posted_at=posted_at,
            assignments=[],
            merge_requests=[],
        )
        session.add(review)

    review.product = post.product
    review.title = post.title
    review.task_url = post.task_url
    review.raw_text = raw_text
    if author_user_id is not None:
        review.author_user_id = author_user_id
    if author_label is not None:
        review.author_label = author_label

    await session.flush()
    _sync_merge_requests(review, post)
    await _sync_assignments(session, review, post, posted_at)
    await session.flush()
    return review


def _sync_merge_requests(review: Review, post: ParsedPost) -> None:
    existing = {(link.project_path, link.iid) for link in review.merge_requests}
    for ref in post.merge_requests:
        if (ref.project_path, ref.iid) in existing:
            continue
        review.merge_requests.append(
            MergeRequestLink(host=ref.host, project_path=ref.project_path, iid=ref.iid)
        )


async def _sync_assignments(
    session: AsyncSession, review: Review, post: ParsedPost, posted_at: datetime
) -> None:
    """Add reviewers named in the post. Existing ones keep their state.

    Reviewers are never removed on edit: someone who already gave a verdict should not
    lose it because the post was reworded.
    """
    existing = {row.mention_key for row in review.assignments}

    for mention in post.reviewers:
        if mention.key in existing:
            continue
        # The author reviewing their own MR is a template artefact, not an assignment.
        if mention.user_id is not None and mention.user_id == review.author_user_id:
            continue

        telegram_user_id = mention.user_id
        if telegram_user_id is None and mention.username:
            user = await repo.get_user_by_username(session, mention.username)
            telegram_user_id = user.telegram_user_id if user else None

        review.assignments.append(
            ReviewerAssignment(
                mention_key=mention.key,
                username=mention.username,
                telegram_user_id=telegram_user_id,
                display_label=mention.label,
                state=ReviewerState.PENDING,
                ball_since=posted_at,
            )
        )


async def apply_verdict(
    session: AsyncSession,
    assignment: ReviewerAssignment,
    event: Event,
    at: datetime | None = None,
    required_approvals: int = 2,
) -> VerdictResult:
    """Run one event through the state machine and persist the outcome.

    Illegal transitions are not errors here — a reviewer pressing 👍 twice, or GitLab
    reporting the same "all resolved" on every poll, is ordinary traffic. They come
    back as `changed=False` so the caller can answer the callback without a fuss.
    """
    moment = at or utcnow()
    current = repo.to_domain(assignment)

    try:
        updated = current.apply(event, moment)
    except IllegalTransition:
        return VerdictResult(assignment=assignment, changed=False)

    if updated is current:
        return VerdictResult(assignment=assignment, changed=False)

    repo.apply_domain(assignment, updated)
    assignment.decided_at = moment

    review = assignment.review
    closed = await _close_if_enough_approvals(session, review, moment, required_approvals)
    await session.flush()
    return VerdictResult(assignment=assignment, changed=True, review_closed=closed)


async def mark_fixes_done(
    session: AsyncSession,
    review: Review,
    at: datetime | None = None,
    only_reviewer_id: int | None = None,
) -> list[ReviewerAssignment]:
    """The author says the comments are addressed — hand the ball to every reviewer
    still sitting on ✍️. Returns the assignments that actually moved."""
    moment = at or utcnow()
    moved: list[ReviewerAssignment] = []

    for assignment in review.assignments:
        if assignment.state is not ReviewerState.CHANGES_REQUESTED:
            continue
        if only_reviewer_id is not None and assignment.telegram_user_id != only_reviewer_id:
            continue
        result = await apply_verdict(session, assignment, Event.FIXES_DONE, moment)
        if result.changed:
            moved.append(assignment)

    await session.flush()
    return moved


async def _close_if_enough_approvals(
    session: AsyncSession, review: Review, at: datetime, required_approvals: int
) -> bool:
    """Team rule: two 👍 close the MR."""
    if review.is_closed:
        return False
    if review.approvals < required_approvals:
        return False
    review.is_closed = True
    review.closed_at = at
    logger.info("review %s closed with %s approvals", review.id, review.approvals)
    return True


async def close_review(session: AsyncSession, review: Review, at: datetime | None = None) -> bool:
    """Manual close — the author is done with it regardless of the approval count."""
    if review.is_closed:
        return False
    review.is_closed = True
    review.closed_at = at or utcnow()
    await session.flush()
    return True


async def reopen_review(session: AsyncSession, review: Review) -> bool:
    if not review.is_closed:
        return False
    review.is_closed = False
    review.closed_at = None
    await session.flush()
    return True


async def link_user_to_assignments(
    session: AsyncSession, telegram_user_id: int, username: str
) -> int:
    """Backfill the telegram id on assignments that only knew a @handle.

    Reviewers are named by handle in the post, but a DM needs a numeric id — which we
    only learn when they talk to the bot. Called on /start and on any button press.
    """
    rows = await repo.unlinked_assignments_for_username(session, username)
    for row in rows:
        row.telegram_user_id = telegram_user_id
    await session.flush()
    return len(rows)
