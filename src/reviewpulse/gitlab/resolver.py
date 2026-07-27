"""Turn GitLab discussion payloads into the one fact the bot cares about:

    "has this reviewer's feedback been addressed?"

A thread belongs to the reviewer who opened it (the author of its root note). The
reviewer's feedback is addressed when every resolvable thread of theirs is resolved —
and it stops being addressed the moment they open a new unresolved one, which is
exactly the "reviewer asked for more changes" case that must silence the nudges.

Pure functions over plain dicts, so the tricky part is testable against recorded
payloads without a GitLab instance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..parsing.gitlab_url import MergeRequestRef


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ThreadState(BaseModel):
    model_config = ConfigDict(frozen=True)

    discussion_id: str
    author_username: str | None
    resolved: bool
    resolved_at: datetime | None
    created_at: datetime | None


class ReviewerThreads(BaseModel):
    """One reviewer's resolvable threads within a single MR (or aggregated across MRs)."""

    model_config = ConfigDict(frozen=True)

    total: int = 0
    unresolved: int = 0
    last_resolved_at: datetime | None = None
    last_opened_at: datetime | None = None

    @property
    def has_threads(self) -> bool:
        return self.total > 0

    @property
    def all_resolved(self) -> bool:
        return self.total > 0 and self.unresolved == 0

    def merge(self, other: ReviewerThreads) -> ReviewerThreads:
        return ReviewerThreads(
            total=self.total + other.total,
            unresolved=self.unresolved + other.unresolved,
            last_resolved_at=_max_time(self.last_resolved_at, other.last_resolved_at),
            last_opened_at=_max_time(self.last_opened_at, other.last_opened_at),
        )


class MergeRequestSnapshot(BaseModel):
    """Everything one poll of one MR tells us."""

    model_config = ConfigDict(frozen=True)

    ref: MergeRequestRef
    blocking_discussions_resolved: bool | None = None
    threads: list[ThreadState] = Field(default_factory=list)

    def for_reviewer(self, gitlab_username: str) -> ReviewerThreads:
        target = gitlab_username.lower().lstrip("@")
        result = ReviewerThreads()
        for thread in self.threads:
            if (thread.author_username or "").lower() != target:
                continue
            result = result.merge(
                ReviewerThreads(
                    total=1,
                    unresolved=0 if thread.resolved else 1,
                    last_resolved_at=thread.resolved_at if thread.resolved else None,
                    last_opened_at=None if thread.resolved else thread.created_at,
                )
            )
        return result


def parse_discussions(ref: MergeRequestRef, payload: list[dict[str, Any]]) -> list[ThreadState]:
    """Collapse each discussion into a single resolvable-thread record.

    Skipped: system notes (GitLab's own "changed the description" entries), individual
    notes that carry no thread, and anything not marked resolvable — none of those
    represent feedback that can be "addressed".
    """
    threads: list[ThreadState] = []
    for discussion in payload:
        notes = [note for note in discussion.get("notes") or [] if not note.get("system")]
        if not notes:
            continue
        root = notes[0]
        if not root.get("resolvable"):
            continue

        # A thread is open while any of its resolvable notes is unresolved.
        resolvable_notes = [note for note in notes if note.get("resolvable")]
        resolved = all(note.get("resolved") for note in resolvable_notes)
        resolved_times = [_parse_time(note.get("resolved_at")) for note in resolvable_notes]

        threads.append(
            ThreadState(
                discussion_id=str(discussion.get("id", "")),
                author_username=(root.get("author") or {}).get("username"),
                resolved=resolved,
                resolved_at=_max_time(*resolved_times) if resolved else None,
                created_at=_parse_time(root.get("created_at")),
            )
        )
    return threads


def snapshot_from_payloads(
    ref: MergeRequestRef,
    merge_request: dict[str, Any] | None,
    discussions: list[dict[str, Any]],
) -> MergeRequestSnapshot:
    return MergeRequestSnapshot(
        ref=ref,
        blocking_discussions_resolved=(merge_request or {}).get("blocking_discussions_resolved"),
        threads=parse_discussions(ref, discussions),
    )


class FeedbackStatus(BaseModel):
    """The verdict handed to the state machine."""

    model_config = ConfigDict(frozen=True)

    addressed: bool
    """Every thread of this reviewer is resolved — safe to nudge them for a re-look."""

    has_open_feedback: bool
    """The reviewer has an unresolved thread — the ball is on the author, stay quiet."""

    known: bool = True
    """False when GitLab could not tell us anything useful; caller should not act."""


def evaluate_reviewer(
    snapshots: list[MergeRequestSnapshot], gitlab_username: str | None
) -> FeedbackStatus:
    """Aggregate across every MR of the review — all of them must be clean.

    Without a Telegram->GitLab mapping we fall back to the MR-wide
    `blocking_discussions_resolved` flag, which is coarser (it counts *everyone's*
    threads) but still beats knowing nothing.
    """
    if not snapshots:
        return FeedbackStatus(addressed=False, has_open_feedback=False, known=False)

    if gitlab_username:
        aggregate = ReviewerThreads()
        for snapshot in snapshots:
            aggregate = aggregate.merge(snapshot.for_reviewer(gitlab_username))
        if aggregate.has_threads:
            return FeedbackStatus(
                addressed=aggregate.all_resolved,
                has_open_feedback=aggregate.unresolved > 0,
            )
        # The reviewer left no threads at all — nothing to be addressed, and the
        # MR-wide flag would be about someone else's comments. Say we don't know.
        return FeedbackStatus(addressed=False, has_open_feedback=False, known=False)

    flags = [
        snapshot.blocking_discussions_resolved
        for snapshot in snapshots
        if snapshot.blocking_discussions_resolved is not None
    ]
    if not flags:
        return FeedbackStatus(addressed=False, has_open_feedback=False, known=False)
    return FeedbackStatus(addressed=all(flags), has_open_feedback=not all(flags))


def _max_time(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None
