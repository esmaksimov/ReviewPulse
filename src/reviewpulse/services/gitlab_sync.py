"""Poll GitLab and translate thread state into state-machine events.

This is the half of the bot that answers "the author fixed everything two hours ago and
the reviewer still hasn't looked" without anyone pressing a button. Feature-flagged:
with GITLAB_ENABLED off (or no token) the bot runs on the card buttons alone.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repo
from ..db.models import Review, ReviewerAssignment, utcnow
from ..domain.state import Event, ReviewerState
from ..gitlab.client import GitLabClient, GitLabError
from ..gitlab.resolver import MergeRequestSnapshot, evaluate_reviewer, snapshot_from_payloads
from ..parsing.gitlab_url import MergeRequestRef
from .reviews import apply_verdict

logger = logging.getLogger(__name__)

#: GitLab may not tell us anything about approved reviewers without risking a flap:
#: a thread someone else reopens must not silently revoke a 👍.
_SYNCABLE = frozenset({ReviewerState.CHANGES_REQUESTED, ReviewerState.AWAITING_RECHECK})


class SyncChange(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    assignment: ReviewerAssignment
    event: Event

    @property
    def became_nudgeable(self) -> bool:
        return self.event is Event.FIXES_DONE


async def sync_open_reviews(
    session: AsyncSession,
    client: GitLabClient,
    now: datetime | None = None,
    approvals_cap: int = 2,
) -> list[SyncChange]:
    moment = now or utcnow()
    changes: list[SyncChange] = []

    for review in await repo.open_reviews_with_merge_requests(session):
        if not any(row.state in _SYNCABLE for row in review.assignments):
            continue  # nothing on this review could move; don't spend the API calls
        try:
            snapshots = await _fetch_snapshots(session, client, review, moment)
        except GitLabError:
            logger.warning("gitlab sync skipped review %s", review.id, exc_info=True)
            continue
        changes.extend(
            await _apply_snapshots(session, review, snapshots, moment, approvals_cap)
        )

    await session.flush()
    return changes


async def _fetch_snapshots(
    session: AsyncSession, client: GitLabClient, review: Review, moment: datetime
) -> list[MergeRequestSnapshot]:
    snapshots: list[MergeRequestSnapshot] = []

    for link in review.merge_requests:
        if link.platform != "gitlab":
            continue  # this poller only ever speaks the GitLab REST API
        ref = MergeRequestRef(host=link.host, project_path=link.project_path, iid=link.iid)
        try:
            merge_request = await client.get_merge_request(ref)
            discussions = await client.get_discussions(ref)
        except GitLabError as exc:
            link.sync_error = str(exc)[:500]
            link.last_synced_at = moment
            raise

        link.sync_error = None
        link.last_synced_at = moment
        link.blocking_discussions_resolved = merge_request.get("blocking_discussions_resolved")
        snapshots.append(snapshot_from_payloads(ref, merge_request, discussions))

    return snapshots


async def _apply_snapshots(
    session: AsyncSession,
    review: Review,
    snapshots: list[MergeRequestSnapshot],
    moment: datetime,
    approvals_cap: int,
) -> list[SyncChange]:
    changes: list[SyncChange] = []

    for assignment in review.assignments:
        if assignment.state not in _SYNCABLE:
            continue

        status = evaluate_reviewer(snapshots, await _gitlab_username(session, assignment))
        if not status.known:
            continue

        event = _event_for(assignment.state, status.addressed, status.has_open_feedback)
        if event is None:
            continue

        result = await apply_verdict(
            session, assignment, event, moment, approvals_cap=approvals_cap
        )
        if result.changed:
            logger.info(
                "gitlab sync moved assignment %s to %s", assignment.id, assignment.state.value
            )
            changes.append(SyncChange(assignment=assignment, event=event))

    return changes


def _event_for(state: ReviewerState, addressed: bool, has_open_feedback: bool) -> Event | None:
    if state is ReviewerState.CHANGES_REQUESTED and addressed:
        return Event.FIXES_DONE
    # The reviewer opened a fresh thread after the fixes landed: they are back in the
    # loop of their own accord, so stop reminding them.
    if state is ReviewerState.AWAITING_RECHECK and has_open_feedback:
        return Event.REQUEST_CHANGES
    return None


async def _gitlab_username(session: AsyncSession, assignment: ReviewerAssignment) -> str | None:
    if assignment.telegram_user_id is None:
        return None
    user = await repo.get_user_by_telegram_id(session, assignment.telegram_user_id)
    return user.gitlab_username if user else None
