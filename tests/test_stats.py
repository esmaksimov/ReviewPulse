"""services.stats.build_report: pure, given a list of AssignmentTransition rows, a
work calendar, and a window, so it never needs a database — just objects shaped like
the real rows.

Deliberately does not go through a real session/`apply_verdict`: the interesting
behaviour here is the pairing/aggregation math, and constructing bare rows makes the
boundary cases (a transition just outside the window, a gap over a weekend) trivial
to set up precisely.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from conftest import MSK, msk
from reviewpulse.db.models import AssignmentTransition, Review, ReviewerAssignment
from reviewpulse.domain.state import Event, ReviewerState
from reviewpulse.domain.workhours import WorkCalendar
from reviewpulse.services.stats import build_report

CALENDAR = WorkCalendar()  # 09:00-18:00 UTC+3 (matches msk()), Mon-Fri


def _assignment(id_: int, *, label: str, created_at: datetime) -> ReviewerAssignment:
    return ReviewerAssignment(
        id=id_,
        mention_key=f"un:{label}",
        display_label=label,
        state=ReviewerState.PENDING,
        ball_since=created_at,
        created_at=created_at,
    )


def _review(id_: int, *, author_user_id: int | None, author_label: str | None) -> Review:
    row = Review(id=id_, channel_chat_id=-1, channel_message_id=id_)
    row.author_user_id = author_user_id
    row.author_label = author_label
    return row


def _transition(
    assignment: ReviewerAssignment,
    review: Review,
    *,
    from_state: ReviewerState,
    to_state: ReviewerState,
    event: Event,
    at: datetime,
) -> AssignmentTransition:
    row = AssignmentTransition(
        assignment_id=assignment.id,
        review_id=review.id,
        from_state=from_state,
        to_state=to_state,
        event=event.value,
        at=at,
    )
    row.assignment = assignment
    row.review = review
    return row


def test_reviewer_response_time_measured_from_assignment_creation() -> None:
    review = _review(1, author_user_id=None, author_label=None)
    assignment = _assignment(1, label="@rev", created_at=msk(27, 9))
    first_verdict = _transition(
        assignment,
        review,
        from_state=ReviewerState.PENDING,
        to_state=ReviewerState.APPROVED,
        event=Event.APPROVE,
        at=msk(27, 11),
    )

    report = build_report([first_verdict], CALENDAR, since=msk(27, 0), until=msk(27, 23))

    assert len(report.reviewer_response_time) == 1
    stat = report.reviewer_response_time[0]
    assert stat.label == "@rev"
    assert stat.median == timedelta(hours=2)
    assert stat.sample_count == 1
    assert report.author_fix_time == []


def test_author_fix_time_paired_from_request_changes_to_fixes_done() -> None:
    review = _review(1, author_user_id=555, author_label="@author")
    assignment = _assignment(1, label="@rev", created_at=msk(27, 9))
    requested = _transition(
        assignment,
        review,
        from_state=ReviewerState.PENDING,
        to_state=ReviewerState.CHANGES_REQUESTED,
        event=Event.REQUEST_CHANGES,
        at=msk(27, 10),
    )
    fixed = _transition(
        assignment,
        review,
        from_state=ReviewerState.CHANGES_REQUESTED,
        to_state=ReviewerState.AWAITING_RECHECK,
        event=Event.FIXES_DONE,
        at=msk(27, 13),
    )

    report = build_report([requested, fixed], CALENDAR, since=msk(27, 0), until=msk(27, 23))

    assert len(report.author_fix_time) == 1
    assert report.author_fix_time[0].label == "@author"
    assert report.author_fix_time[0].median == timedelta(hours=3)
    # The same two rows also answer "how fast did the reviewer first respond".
    assert len(report.reviewer_response_time) == 1
    assert report.reviewer_response_time[0].median == timedelta(hours=1)


def test_fix_time_sample_dropped_when_the_author_is_unknown() -> None:
    """No "Автор:" line ever resolved — see services.reviews._sync_author — so there
    is nobody to attribute this sample to."""
    review = _review(1, author_user_id=None, author_label=None)
    assignment = _assignment(1, label="@rev", created_at=msk(27, 9))
    requested = _transition(
        assignment,
        review,
        from_state=ReviewerState.PENDING,
        to_state=ReviewerState.CHANGES_REQUESTED,
        event=Event.REQUEST_CHANGES,
        at=msk(27, 10),
    )
    fixed = _transition(
        assignment,
        review,
        from_state=ReviewerState.CHANGES_REQUESTED,
        to_state=ReviewerState.AWAITING_RECHECK,
        event=Event.FIXES_DONE,
        at=msk(27, 13),
    )

    report = build_report([requested, fixed], CALENDAR, since=msk(27, 0), until=msk(27, 23))
    assert report.author_fix_time == []


def test_a_transition_from_before_the_window_is_not_mistaken_for_a_first_response() -> None:
    """`transitions_between` only ever hands back rows inside [since, until) — so if
    an assignment's real first transition happened earlier, the row we do have is not
    "from PENDING" and must not be counted as a response-time sample."""
    review = _review(1, author_user_id=555, author_label="@author")
    assignment = _assignment(1, label="@rev", created_at=msk(27, 0))
    # This assignment's actual first transition (PENDING -> CHANGES_REQUESTED)
    # happened before `since` and so is not in the list at all — only the second one
    # (an un-approve, APPROVED -> CHANGES_REQUESTED) falls inside the window.
    only_row_in_window = _transition(
        assignment,
        review,
        from_state=ReviewerState.APPROVED,
        to_state=ReviewerState.CHANGES_REQUESTED,
        event=Event.REQUEST_CHANGES,
        at=msk(27, 15),
    )

    report = build_report(
        [only_row_in_window], CALENDAR, since=msk(27, 10), until=msk(27, 23)
    )

    assert report.reviewer_response_time == []
    assert report.author_fix_time == []


def test_ranks_slowest_median_first() -> None:
    review = _review(1, author_user_id=None, author_label=None)
    fast = _assignment(1, label="@fast", created_at=msk(27, 9))
    slow = _assignment(2, label="@slow", created_at=msk(27, 9))
    rows = [
        _transition(
            fast,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=msk(27, 10),
        ),
        _transition(
            slow,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=msk(27, 16),
        ),
    ]

    report = build_report(rows, CALENDAR, since=msk(27, 0), until=msk(27, 23))

    assert [stat.label for stat in report.reviewer_response_time] == ["@slow", "@fast"]


def test_multiple_samples_for_the_same_person_are_combined_by_median() -> None:
    review = _review(1, author_user_id=None, author_label=None)
    a = _assignment(1, label="@rev", created_at=msk(27, 9))
    b = _assignment(2, label="@rev", created_at=msk(27, 9))
    rows = [
        _transition(
            a,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=msk(27, 11),
        ),
        _transition(
            b,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=msk(27, 13),
        ),
    ]

    report = build_report(rows, CALENDAR, since=msk(27, 0), until=msk(27, 23))

    assert len(report.reviewer_response_time) == 1
    stat = report.reviewer_response_time[0]
    assert stat.sample_count == 2
    # For exactly two samples the median (the average of the two middle values) and
    # the mean happen to coincide - see the next test for a case where they don't.
    assert stat.median == timedelta(hours=3)  # (2h + 4h) / 2


def test_the_median_is_not_dragged_by_a_single_outlier() -> None:
    """This is why it's a median and not a mean: sample counts per person are often
    just 1-2 in a report window, so one very slow (or very fast) response can swing a
    plain average hard enough to be misleading."""
    review = _review(1, author_user_id=None, author_label=None)
    fast = _assignment(1, label="@rev", created_at=msk(27, 9))
    middle = _assignment(2, label="@rev", created_at=msk(27, 9))
    outlier = _assignment(3, label="@rev", created_at=msk(27, 9))
    rows = [
        _transition(
            fast,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=msk(27, 10),  # 1h
        ),
        _transition(
            middle,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=msk(27, 11),  # 2h
        ),
        _transition(
            outlier,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=msk(27, 17),  # 8h
        ),
    ]

    report = build_report(rows, CALENDAR, since=msk(27, 0), until=msk(27, 23))

    stat = report.reviewer_response_time[0]
    assert stat.sample_count == 3
    assert stat.median == timedelta(hours=2), "the middle value, not the ~3.7h mean"


def test_an_overnight_gap_only_counts_the_working_minutes_either_side() -> None:
    """"Changes requested" at 17:30, answered 09:30 the next morning, is half an hour
    late on each side of the night - not the ~16h wall-clock gap in between."""
    review = _review(1, author_user_id=555, author_label="@author")
    assignment = _assignment(1, label="@rev", created_at=msk(27, 9))
    requested = _transition(
        assignment,
        review,
        from_state=ReviewerState.PENDING,
        to_state=ReviewerState.CHANGES_REQUESTED,
        event=Event.REQUEST_CHANGES,
        at=msk(27, 17, 30),
    )
    fixed = _transition(
        assignment,
        review,
        from_state=ReviewerState.CHANGES_REQUESTED,
        to_state=ReviewerState.AWAITING_RECHECK,
        event=Event.FIXES_DONE,
        at=msk(28, 9, 30),
    )

    report = build_report([requested, fixed], CALENDAR, since=msk(27, 0), until=msk(28, 23))

    assert report.author_fix_time[0].median == timedelta(hours=1)  # 30min + 30min


def test_a_weekend_between_the_request_and_the_response_does_not_count() -> None:
    """The scenario this whole change exists for: changes requested late Friday,
    answered first thing Monday, must not read as if the reviewer sat on it for a
    weekend-long stretch."""
    review = _review(1, author_user_id=555, author_label="@author")
    assignment = _assignment(1, label="@rev", created_at=msk(31, 9))
    friday_evening = msk(31, 17, 30)  # 2026-07-31 is a Friday
    monday_morning = datetime(2026, 8, 3, 9, 30, tzinfo=MSK)  # the following Monday
    requested = _transition(
        assignment,
        review,
        from_state=ReviewerState.PENDING,
        to_state=ReviewerState.CHANGES_REQUESTED,
        event=Event.REQUEST_CHANGES,
        at=friday_evening,
    )
    fixed = _transition(
        assignment,
        review,
        from_state=ReviewerState.CHANGES_REQUESTED,
        to_state=ReviewerState.AWAITING_RECHECK,
        event=Event.FIXES_DONE,
        at=monday_morning,
    )

    report = build_report(
        [requested, fixed], CALENDAR, since=friday_evening, until=monday_morning
    )

    assert report.author_fix_time[0].median == timedelta(hours=1)  # 30min Fri + 30min Mon


def test_empty_report_has_no_samples() -> None:
    report = build_report([], CALENDAR, since=msk(27, 0), until=msk(27, 23))
    assert report.is_empty
    assert report.author_fix_time == []
    assert report.reviewer_response_time == []
