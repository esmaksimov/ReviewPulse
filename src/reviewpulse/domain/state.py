"""The reviewer state machine — the heart of ReviewPulse.

State lives on the (review, reviewer) pair, not on the review. Two reviewers on the
same MR are independent: one may have approved while the other is still holding the ball.

    PENDING           --[👍]---------------------> APPROVED
    PENDING           --[✍️]---------------------> CHANGES_REQUESTED
    CHANGES_REQUESTED --[fixes done]-------------> AWAITING_RECHECK
    AWAITING_RECHECK  --[👍]---------------------> APPROVED
    AWAITING_RECHECK  --[✍️]---------------------> CHANGES_REQUESTED

The last edge is the subtle one. It covers "the reviewer looked at the fixes and asked
for more changes": the ball goes back to the author and the reviewer stops being nudged.
Without it the bot would keep pinging a reviewer who has already done their part.

Pure module: functions take a state plus a moment and return a new state. Persistence,
Telegram and GitLab all live elsewhere and merely call in here.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ReviewerState(StrEnum):
    PENDING = "pending"
    """No verdict yet. The reviewer owes a first look."""

    CHANGES_REQUESTED = "changes_requested"
    """✍️ — the author owes fixes. The reviewer is not nudged."""

    AWAITING_RECHECK = "awaiting_recheck"
    """Fixes are in but ✍️ still stands. The reviewer owes a second look."""

    APPROVED = "approved"
    """👍 — done, unless the reviewer explicitly takes it back."""


#: States in which the reviewer is the one holding the ball and may be nudged.
NUDGEABLE: frozenset[ReviewerState] = frozenset(
    {ReviewerState.PENDING, ReviewerState.AWAITING_RECHECK}
)


class Event(StrEnum):
    APPROVE = "approve"
    """Reviewer pressed 👍."""

    REQUEST_CHANGES = "request_changes"
    """Reviewer pressed ✍️, or opened a fresh unresolved thread in GitLab."""

    FIXES_DONE = "fixes_done"
    """Author pressed "Поправил", or GitLab reports every thread of theirs resolved."""


class IllegalTransition(Exception):
    """Raised when an event cannot be applied to the current state."""

    def __init__(self, state: ReviewerState, event: Event) -> None:
        super().__init__(f"cannot apply {event.value} to {state.value}")
        self.state = state
        self.event = event


_TRANSITIONS: dict[tuple[ReviewerState, Event], ReviewerState] = {
    (ReviewerState.PENDING, Event.APPROVE): ReviewerState.APPROVED,
    (ReviewerState.PENDING, Event.REQUEST_CHANGES): ReviewerState.CHANGES_REQUESTED,
    (ReviewerState.CHANGES_REQUESTED, Event.APPROVE): ReviewerState.APPROVED,
    (ReviewerState.CHANGES_REQUESTED, Event.FIXES_DONE): ReviewerState.AWAITING_RECHECK,
    (ReviewerState.AWAITING_RECHECK, Event.APPROVE): ReviewerState.APPROVED,
    (ReviewerState.AWAITING_RECHECK, Event.REQUEST_CHANGES): ReviewerState.CHANGES_REQUESTED,
    # Un-approve, for the mis-click. Only ever driven by the button: the GitLab sync
    # deliberately skips approved reviewers so a stale thread cannot flap the state.
    (ReviewerState.APPROVED, Event.REQUEST_CHANGES): ReviewerState.CHANGES_REQUESTED,
}


class Assignment(BaseModel):
    """The escalation-relevant slice of one (review, reviewer) pair.

    `ball_since` is when the reviewer last became responsible; the SLA is measured
    from it in working time. It resets on every transition *into* a nudgeable state,
    so a reviewer who asks for more changes and later gets them re-submitted starts
    a fresh clock rather than being instantly overdue.
    """

    model_config = ConfigDict(frozen=True)

    state: ReviewerState
    ball_since: datetime
    last_nudge_at: datetime | None = None
    nudges_today: int = 0
    snoozed_until: datetime | None = None

    def apply(self, event: Event, at: datetime) -> Assignment:
        try:
            new_state = _TRANSITIONS[(self.state, event)]
        except KeyError:
            raise IllegalTransition(self.state, event) from None

        if new_state is self.state:
            return self

        # Entering a nudgeable state restarts the SLA clock and the nudge budget;
        # handing the ball back to the author clears any pending nudge bookkeeping.
        return self.model_copy(
            update={
                "state": new_state,
                "ball_since": at,
                "last_nudge_at": None,
                "nudges_today": 0,
                "snoozed_until": None,
            }
        )

    def can_apply(self, event: Event) -> bool:
        return (self.state, event) in _TRANSITIONS

    @property
    def holds_ball(self) -> bool:
        return self.state in NUDGEABLE

    @property
    def is_final(self) -> bool:
        return self.state is ReviewerState.APPROVED


def initial(at: datetime) -> Assignment:
    return Assignment(state=ReviewerState.PENDING, ball_since=at)
