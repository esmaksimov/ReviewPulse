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
from ..domain.state import NUDGEABLE, Event, IllegalTransition, ReviewerState
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
    is_new = review is None
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
    await _sync_author(session, review, post)
    _sync_merge_requests(review, post)
    await _sync_assignments(session, review, post, posted_at)
    await session.flush()

    if is_new:
        logger.info(
            "tracking new review %s: %s reviewer(s), %s MR(s), channel_message_id=%s",
            review.id,
            len(review.assignments),
            len(review.merge_requests),
            channel_message_id,
        )
    return review


async def _sync_author(session: AsyncSession, review: Review, post: ParsedPost) -> None:
    """Resolve the author to a Telegram id, so "changes requested"/"review approved"
    DMs and the /status entry have somewhere to go.

    Two independent sources feed `author_label`: an opt-in "Автор:" line in the post
    (`post.author`, handled below), and the channel's own post signature
    (`author_label=message.author_signature`, set by the caller in
    `create_or_update_review` before this runs) — most posts carry the latter and
    nothing else. Neither is a Telegram id by itself. An "Автор: @handle" line
    resolves the same way a reviewer's @handle does; anything else — a signature, or
    a bare display name on the "Автор:" line ("Alice", not "@alice"), which
    `_find_author` deliberately declines to guess a handle for — falls back to a
    unique-full-name match (`repo.get_unique_user_by_full_name`). Genuinely
    unresolvable only when no source named anyone, or the name is shared by more than
    one known user.
    """
    mention = post.author
    if mention is not None:
        review.author_username = mention.username
        if review.author_label is None:
            review.author_label = mention.label

        if mention.user_id is not None:
            review.author_user_id = mention.user_id
        elif mention.username:
            user = await repo.get_user_by_username(session, mention.username)
            if user is not None:
                review.author_user_id = user.telegram_user_id

    if review.author_user_id is None and review.author_label:
        user = await repo.get_unique_user_by_full_name(session, review.author_label)
        if user is not None:
            review.author_user_id = user.telegram_user_id


def _sync_merge_requests(review: Review, post: ParsedPost) -> None:
    existing = {(link.project_path, link.iid) for link in review.merge_requests}
    for ref in post.merge_requests:
        if (ref.project_path, ref.iid) in existing:
            continue
        review.merge_requests.append(
            MergeRequestLink(
                host=ref.host, project_path=ref.project_path, iid=ref.iid, platform=ref.platform
            )
        )


async def _sync_assignments(
    session: AsyncSession, review: Review, post: ParsedPost, posted_at: datetime
) -> None:
    """Add reviewers newly named in the post; drop ones no longer named.

    A dropped reviewer is never deleted outright — someone who already gave a verdict
    should not lose it because the post was reworded — it is just marked `removed_at`,
    which excludes it from quorum, the card and nudges (see `Review.approvals`,
    `approvals_needed`, `repo.nudgeable_assignments`). Naming the same handle again in
    a later edit clears that mark.

    This reconciliation only runs when the post has an explicit reviewer-labelled
    line (`has_labelled_reviewers`): that is a deliberate declaration of who reviews
    this. Without one, `post.reviewers` is just whatever @handles happen to float in
    the text — see `_find_reviewers`'s whole-post fallback — far too noisy a signal to
    drop someone over.
    """
    by_key = {row.mention_key: row for row in review.assignments}
    named_keys: set[str] = set()

    for mention in post.reviewers:
        named_keys.add(mention.key)
        existing_row = by_key.get(mention.key)
        if existing_row is not None:
            if existing_row.removed_at is not None:
                existing_row.removed_at = None
                if existing_row.state in NUDGEABLE:
                    existing_row.ball_since = posted_at
            continue
        # The author reviewing their own MR is a template artefact, not an assignment.
        is_author = (
            mention.user_id is not None and mention.user_id == review.author_user_id
        ) or (
            mention.username is not None
            and review.author_username is not None
            and mention.username.lower() == review.author_username.lower()
        )
        if is_author:
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

    if post.has_labelled_reviewers:
        for row in review.assignments:
            if row.mention_key not in named_keys and row.removed_at is None:
                row.removed_at = posted_at


async def apply_verdict(
    session: AsyncSession,
    assignment: ReviewerAssignment,
    event: Event,
    at: datetime | None = None,
    approvals_cap: int = 2,
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

    from_state = assignment.state
    repo.apply_domain(assignment, updated)
    assignment.decided_at = moment
    await repo.record_transition(
        session, assignment, from_state=from_state, to_state=updated.state, event=event, at=moment
    )

    review = assignment.review
    closed = await _close_if_enough_approvals(session, review, moment, approvals_cap)
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


def approvals_needed(review: Review, cap: int) -> int:
    """How many 👍 this specific review needs to close.

    List one reviewer and their approval alone is enough — waiting for a second
    verdict that was never coming just leaves the review stuck. List two (or more)
    and it takes that many, up to `cap` (default 2): naming a long list of reviewers
    doesn't gate the review on unanimous approval, it just needs the team's usual quorum.

    Only counts reviewers currently named on the post (`removed_at is None`) — a
    reviewer dropped in a later edit must not keep propping up the denominator, or a
    stale approval from before they were dropped could count toward a quorum the
    people actually on the review never reached.
    """
    active = sum(1 for row in review.assignments if row.removed_at is None)
    return max(1, min(active, cap))


async def _close_if_enough_approvals(
    session: AsyncSession, review: Review, at: datetime, approvals_cap: int
) -> bool:
    if review.is_closed:
        return False
    if review.approvals < approvals_needed(review, approvals_cap):
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


async def link_author_to_reviews(
    session: AsyncSession, telegram_user_id: int, username: str
) -> int:
    """Backfill the telegram id onto reviews whose "Автор:" line named this handle
    before this person had ever talked to the bot. Mirrors `link_user_to_assignments`
    for reviewers; called from the same places (/start, any button press).
    """
    reviews = await repo.unlinked_authored_reviews_for_username(session, username)
    for review in reviews:
        review.author_user_id = telegram_user_id
    await session.flush()
    return len(reviews)


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
