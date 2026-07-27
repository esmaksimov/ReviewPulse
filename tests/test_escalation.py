from datetime import datetime, timedelta, timezone

import pytest

from reviewpulse.domain.escalation import EscalationPolicy, NudgeReason
from reviewpulse.domain.state import Assignment, Event, ReviewerState, initial
from reviewpulse.domain.workhours import WorkCalendar

MSK = timezone(timedelta(hours=3))


def msk(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=MSK)


@pytest.fixture
def policy() -> EscalationPolicy:
    return EscalationPolicy(calendar=WorkCalendar())  # 2h SLA, 20min interval, 8/day


def test_quiet_before_the_sla_expires(policy: EscalationPolicy) -> None:
    assignment = initial(msk(27, 10, 0))
    assert policy.evaluate(assignment, msk(27, 11, 59)) is None


def test_nudges_once_the_sla_expires(policy: EscalationPolicy) -> None:
    assignment = initial(msk(27, 10, 0))
    nudge = policy.evaluate(assignment, msk(27, 12, 1))
    assert nudge is not None
    assert nudge.reason is NudgeReason.NO_REACTION


def test_never_nudges_outside_working_hours(policy: EscalationPolicy) -> None:
    """Long overdue, but it is 03:00 — silence outranks everything."""
    assignment = initial(msk(27, 10, 0))
    assert policy.evaluate(assignment, msk(28, 3, 0)) is None
    assert policy.evaluate(assignment, msk(28, 9, 1)) is not None


def test_never_nudges_at_the_weekend(policy: EscalationPolicy) -> None:
    assignment = initial(msk(31, 10, 0))  # Friday
    assert policy.evaluate(assignment, msk(31, 15, 0)) is not None
    assert policy.evaluate(assignment, datetime(2026, 8, 1, 12, 0, tzinfo=MSK)) is None


def test_sla_consumes_working_time_only(policy: EscalationPolicy) -> None:
    """Posted 17:30 Friday. At 09:31 Monday only 1h29m of work time has passed."""
    assignment = initial(msk(31, 17, 30))
    assert policy.evaluate(assignment, datetime(2026, 8, 3, 9, 31, tzinfo=MSK)) is None
    assert policy.evaluate(assignment, datetime(2026, 8, 3, 10, 31, tzinfo=MSK)) is not None


def test_author_holding_the_ball_is_never_nudged(policy: EscalationPolicy) -> None:
    assignment = initial(msk(27, 10, 0)).apply(Event.REQUEST_CHANGES, msk(27, 10, 30))
    assert policy.evaluate(assignment, msk(28, 17, 0)) is None, "days later, still quiet"


def test_stale_changes_requested_gets_its_own_reason(policy: EscalationPolicy) -> None:
    assignment = (
        initial(msk(27, 10, 0))
        .apply(Event.REQUEST_CHANGES, msk(27, 10, 30))
        .apply(Event.FIXES_DONE, msk(27, 11, 0))
    )
    assert policy.evaluate(assignment, msk(27, 12, 30)) is None, "recheck SLA not up yet"

    nudge = policy.evaluate(assignment, msk(27, 13, 1))
    assert nudge is not None
    assert nudge.reason is NudgeReason.STALE_CHANGES_REQUESTED


def test_reviewer_asking_for_more_changes_stops_the_pings(policy: EscalationPolicy) -> None:
    addressed = (
        initial(msk(27, 9, 0))
        .apply(Event.REQUEST_CHANGES, msk(27, 9, 30))
        .apply(Event.FIXES_DONE, msk(27, 10, 0))
    )
    assert policy.evaluate(addressed, msk(27, 14, 0)) is not None

    reopened = addressed.apply(Event.REQUEST_CHANGES, msk(27, 14, 5))
    assert policy.evaluate(reopened, msk(27, 17, 0)) is None
    assert policy.evaluate(reopened, msk(29, 17, 0)) is None


def test_respects_the_repeat_interval(policy: EscalationPolicy) -> None:
    assignment = Assignment(
        state=ReviewerState.PENDING,
        ball_since=msk(27, 9, 0),
        last_nudge_at=msk(27, 12, 0),
        nudges_today=1,
    )
    assert policy.evaluate(assignment, msk(27, 12, 15)) is None
    assert policy.evaluate(assignment, msk(27, 12, 21)) is not None


def test_daily_budget_caps_the_spam(policy: EscalationPolicy) -> None:
    assignment = Assignment(
        state=ReviewerState.PENDING,
        ball_since=msk(27, 9, 0),
        last_nudge_at=msk(27, 15, 0),
        nudges_today=8,
    )
    assert policy.evaluate(assignment, msk(27, 16, 0)) is None


def test_daily_budget_resets_the_next_working_day(policy: EscalationPolicy) -> None:
    assignment = Assignment(
        state=ReviewerState.PENDING,
        ball_since=msk(27, 9, 0),
        last_nudge_at=msk(27, 17, 0),
        nudges_today=8,
    )
    assert policy.evaluate(assignment, msk(28, 9, 30)) is not None


def test_snooze_is_honoured(policy: EscalationPolicy) -> None:
    assignment = Assignment(
        state=ReviewerState.PENDING,
        ball_since=msk(27, 9, 0),
        snoozed_until=msk(27, 16, 0),
    )
    assert policy.evaluate(assignment, msk(27, 15, 30)) is None
    assert policy.evaluate(assignment, msk(27, 16, 1)) is not None


def test_overdue_amount_is_reported_in_working_time(policy: EscalationPolicy) -> None:
    assignment = initial(msk(27, 16, 0))
    nudge = policy.evaluate(assignment, msk(28, 10, 0))
    assert nudge is not None
    # 16:00->18:00 is the 2h SLA; 09:00->10:00 next day is the overdue hour.
    assert nudge.overdue_by == timedelta(hours=1)


def test_deadline_is_exposed_for_the_status_card(policy: EscalationPolicy) -> None:
    deadline = policy.deadline_for(initial(msk(27, 17, 30)))
    assert deadline.astimezone(MSK) == msk(28, 10, 30)
