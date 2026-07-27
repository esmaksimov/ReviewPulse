from datetime import datetime, timedelta, timezone

import pytest

from reviewpulse.domain.state import (
    Assignment,
    Event,
    IllegalTransition,
    ReviewerState,
    initial,
)

MSK = timezone(timedelta(hours=3))


def msk(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=MSK)


def test_happy_path_approval() -> None:
    assignment = initial(msk(27, 10, 0)).apply(Event.APPROVE, msk(27, 11, 0))
    assert assignment.state is ReviewerState.APPROVED
    assert assignment.is_final
    assert not assignment.holds_ball


def test_changes_requested_moves_the_ball_to_the_author() -> None:
    assignment = initial(msk(27, 10, 0)).apply(Event.REQUEST_CHANGES, msk(27, 11, 0))
    assert assignment.state is ReviewerState.CHANGES_REQUESTED
    assert not assignment.holds_ball, "the reviewer must not be nudged while fixes are pending"


def test_fixes_done_hands_the_ball_back_and_restarts_the_clock() -> None:
    assignment = (
        initial(msk(27, 10, 0))
        .apply(Event.REQUEST_CHANGES, msk(27, 11, 0))
        .apply(Event.FIXES_DONE, msk(27, 15, 0))
    )
    assert assignment.state is ReviewerState.AWAITING_RECHECK
    assert assignment.holds_ball
    assert assignment.ball_since == msk(27, 15, 0), "SLA runs from when the fixes landed"


def test_reviewer_asking_for_more_changes_silences_the_nudges() -> None:
    """The case the whole bot exists for: don't keep pinging a reviewer who came back."""
    assignment = (
        initial(msk(27, 10, 0))
        .apply(Event.REQUEST_CHANGES, msk(27, 11, 0))
        .apply(Event.FIXES_DONE, msk(27, 12, 0))
    )
    assignment = Assignment(
        state=assignment.state,
        ball_since=assignment.ball_since,
        last_nudge_at=msk(27, 14, 30),
        nudges_today=3,
        snoozed_until=None,
    )

    after = assignment.apply(Event.REQUEST_CHANGES, msk(27, 15, 0))

    assert after.state is ReviewerState.CHANGES_REQUESTED
    assert not after.holds_ball
    assert after.last_nudge_at is None, "nudge bookkeeping is reset with the ball"
    assert after.nudges_today == 0


def test_second_round_of_fixes_reactivates_nudging() -> None:
    assignment = (
        initial(msk(27, 10, 0))
        .apply(Event.REQUEST_CHANGES, msk(27, 11, 0))
        .apply(Event.FIXES_DONE, msk(27, 12, 0))
        .apply(Event.REQUEST_CHANGES, msk(27, 13, 0))
        .apply(Event.FIXES_DONE, msk(27, 16, 0))
    )
    assert assignment.state is ReviewerState.AWAITING_RECHECK
    assert assignment.ball_since == msk(27, 16, 0)


def test_approved_only_reopens_via_an_explicit_request_for_changes() -> None:
    approved = initial(msk(27, 10, 0)).apply(Event.APPROVE, msk(27, 11, 0))

    for event in (Event.APPROVE, Event.FIXES_DONE):
        assert not approved.can_apply(event)
        with pytest.raises(IllegalTransition):
            approved.apply(event, msk(27, 12, 0))

    revoked = approved.apply(Event.REQUEST_CHANGES, msk(27, 12, 0))
    assert revoked.state is ReviewerState.CHANGES_REQUESTED, "recovery from a mis-clicked 👍"


def test_fixes_done_on_a_pending_reviewer_is_rejected() -> None:
    """Nobody asked for changes yet, so "author fixed it" is meaningless here."""
    pending = initial(msk(27, 10, 0))
    assert not pending.can_apply(Event.FIXES_DONE)
    with pytest.raises(IllegalTransition):
        pending.apply(Event.FIXES_DONE, msk(27, 12, 0))


def test_repeated_fixes_done_is_idempotent() -> None:
    """GitLab polling re-reports the same "all resolved" every few minutes."""
    assignment = (
        initial(msk(27, 10, 0))
        .apply(Event.REQUEST_CHANGES, msk(27, 11, 0))
        .apply(Event.FIXES_DONE, msk(27, 12, 0))
    )
    assert not assignment.can_apply(Event.FIXES_DONE)


def test_pressing_the_same_button_twice_does_not_reset_the_clock() -> None:
    once = initial(msk(27, 10, 0)).apply(Event.REQUEST_CHANGES, msk(27, 11, 0))
    assert not once.can_apply(Event.REQUEST_CHANGES)
