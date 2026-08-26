"""services.stats.build_report: pure, given a list of AssignmentTransition rows and a
window, so it never needs a database — just objects shaped like the real rows.

Deliberately does not go through a real session/`apply_verdict`: the interesting
behaviour here is the pairing/aggregation math, and constructing bare rows makes the
boundary cases (a transition just outside the window) trivial to set up precisely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from reviewpulse.db.models import AssignmentTransition, Review, ReviewerAssignment
from reviewpulse.domain.state import Event, ReviewerState
from reviewpulse.services.stats import build_report


def _at(hour: int) -> datetime:
    return datetime(2026, 8, 1, hour, tzinfo=UTC)


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
    assignment = _assignment(1, label="@rev", created_at=_at(9))
    first_verdict = _transition(
        assignment,
        review,
        from_state=ReviewerState.PENDING,
        to_state=ReviewerState.APPROVED,
        event=Event.APPROVE,
        at=_at(11),
    )

    report = build_report([first_verdict], since=_at(0), until=_at(23))

    assert len(report.reviewer_response_time) == 1
    stat = report.reviewer_response_time[0]
    assert stat.label == "@rev"
    assert stat.average == timedelta(hours=2)
    assert stat.sample_count == 1
    assert report.author_fix_time == []


def test_author_fix_time_paired_from_request_changes_to_fixes_done() -> None:
    review = _review(1, author_user_id=555, author_label="@author")
    assignment = _assignment(1, label="@rev", created_at=_at(9))
    requested = _transition(
        assignment,
        review,
        from_state=ReviewerState.PENDING,
        to_state=ReviewerState.CHANGES_REQUESTED,
        event=Event.REQUEST_CHANGES,
        at=_at(10),
    )
    fixed = _transition(
        assignment,
        review,
        from_state=ReviewerState.CHANGES_REQUESTED,
        to_state=ReviewerState.AWAITING_RECHECK,
        event=Event.FIXES_DONE,
        at=_at(13),
    )

    report = build_report([requested, fixed], since=_at(0), until=_at(23))

    assert len(report.author_fix_time) == 1
    assert report.author_fix_time[0].label == "@author"
    assert report.author_fix_time[0].average == timedelta(hours=3)
    # The same two rows also answer "how fast did the reviewer first respond".
    assert len(report.reviewer_response_time) == 1
    assert report.reviewer_response_time[0].average == timedelta(hours=1)


def test_fix_time_sample_dropped_when_the_author_is_unknown() -> None:
    """No "Автор:" line ever resolved — see services.reviews._sync_author — so there
    is nobody to attribute this sample to."""
    review = _review(1, author_user_id=None, author_label=None)
    assignment = _assignment(1, label="@rev", created_at=_at(9))
    requested = _transition(
        assignment,
        review,
        from_state=ReviewerState.PENDING,
        to_state=ReviewerState.CHANGES_REQUESTED,
        event=Event.REQUEST_CHANGES,
        at=_at(10),
    )
    fixed = _transition(
        assignment,
        review,
        from_state=ReviewerState.CHANGES_REQUESTED,
        to_state=ReviewerState.AWAITING_RECHECK,
        event=Event.FIXES_DONE,
        at=_at(13),
    )

    report = build_report([requested, fixed], since=_at(0), until=_at(23))
    assert report.author_fix_time == []


def test_a_transition_from_before_the_window_is_not_mistaken_for_a_first_response() -> None:
    """`transitions_between` only ever hands back rows inside [since, until) — so if
    an assignment's real first transition happened earlier, the row we do have is not
    "from PENDING" and must not be counted as a response-time sample."""
    review = _review(1, author_user_id=555, author_label="@author")
    assignment = _assignment(1, label="@rev", created_at=_at(0))
    # This assignment's actual first transition (PENDING -> CHANGES_REQUESTED)
    # happened before `since` and so is not in the list at all — only the second one
    # (an un-approve, APPROVED -> CHANGES_REQUESTED) falls inside the window.
    only_row_in_window = _transition(
        assignment,
        review,
        from_state=ReviewerState.APPROVED,
        to_state=ReviewerState.CHANGES_REQUESTED,
        event=Event.REQUEST_CHANGES,
        at=_at(15),
    )

    report = build_report([only_row_in_window], since=_at(10), until=_at(23))

    assert report.reviewer_response_time == []
    assert report.author_fix_time == []


def test_ranks_slowest_average_first() -> None:
    review = _review(1, author_user_id=None, author_label=None)
    fast = _assignment(1, label="@fast", created_at=_at(9))
    slow = _assignment(2, label="@slow", created_at=_at(9))
    rows = [
        _transition(
            fast,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=_at(10),
        ),
        _transition(
            slow,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=_at(16),
        ),
    ]

    report = build_report(rows, since=_at(0), until=_at(23))

    assert [stat.label for stat in report.reviewer_response_time] == ["@slow", "@fast"]


def test_multiple_samples_for_the_same_person_are_averaged() -> None:
    review = _review(1, author_user_id=None, author_label=None)
    a = _assignment(1, label="@rev", created_at=_at(9))
    b = _assignment(2, label="@rev", created_at=_at(9))
    rows = [
        _transition(
            a,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=_at(11),
        ),
        _transition(
            b,
            review,
            from_state=ReviewerState.PENDING,
            to_state=ReviewerState.APPROVED,
            event=Event.APPROVE,
            at=_at(13),
        ),
    ]

    report = build_report(rows, since=_at(0), until=_at(23))

    assert len(report.reviewer_response_time) == 1
    stat = report.reviewer_response_time[0]
    assert stat.sample_count == 2
    assert stat.average == timedelta(hours=3)  # (2h + 4h) / 2


def test_empty_report_has_no_samples() -> None:
    report = build_report([], since=_at(0), until=_at(23))
    assert report.is_empty
    assert report.author_fix_time == []
    assert report.reviewer_response_time == []
