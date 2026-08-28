"""Per-person timing stats: who takes long to fix, who takes long to first respond.

Built entirely from `AssignmentTransition` rows (`db.repo.transitions_between`) — see
that model's docstring for why this can only ever look forward from when it was
added, never backfill reviews that happened before it existed.

Every duration is *working* time, via the same `WorkCalendar` the escalation policy
uses for deadlines — not wall-clock. A "changes requested" at 17:30 Friday, answered
09:00 Monday, is 30 minutes late, not a weekend-long one; counting the calendar gap
instead would treat every Friday-afternoon review as if the reviewer had been
unusually slow.

Pure module, like `domain.state`: takes rows plus a window, returns a `StatsReport`.
Telegram and the scheduler live elsewhere and merely call in here.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..db.models import AssignmentTransition
from ..domain.state import Event, ReviewerState
from ..domain.workhours import WorkCalendar


@dataclass(frozen=True)
class PersonStat:
    label: str
    sample_count: int
    durations: tuple[timedelta, ...]

    @property
    def median(self) -> timedelta:
        """The middle duration, not the mean: one bad (or great) day skews a mean
        hard when `sample_count` is this small (often 1-2 over a report window), and
        the median shrugs it off."""
        return statistics.median(self.durations) if self.durations else timedelta()


@dataclass(frozen=True)
class StatsReport:
    since: datetime
    until: datetime
    #: Slowest median first.
    author_fix_time: list[PersonStat]
    reviewer_response_time: list[PersonStat]

    @property
    def is_empty(self) -> bool:
        return not self.author_fix_time and not self.reviewer_response_time


def build_report(
    transitions: list[AssignmentTransition],
    calendar: WorkCalendar,
    *,
    since: datetime,
    until: datetime,
) -> StatsReport:
    """`transitions` must be ordered per-assignment (see `transitions_between`) and
    should cover exactly `[since, until)` — a transition just outside that window,
    on either side of a pair, silently drops the pair rather than mis-measuring it
    (see the two loops below for exactly where that guard lives).
    """
    by_assignment: dict[int, list[AssignmentTransition]] = defaultdict(list)
    for row in transitions:
        by_assignment[row.assignment_id].append(row)

    fix_samples: dict[str, list[timedelta]] = defaultdict(list)
    response_samples: dict[str, list[timedelta]] = defaultdict(list)

    for rows in by_assignment.values():
        first = rows[0]
        # Only a genuine first-ever verdict starts from PENDING. If the window began
        # mid-history, `rows[0]` here is merely the first row *we fetched* — counting
        # it as a response time would measure against the wrong starting point.
        if first.from_state is ReviewerState.PENDING:
            response_samples[first.assignment.display_label].append(
                _working_time(calendar, first.assignment.created_at, first.at)
            )

        for prev, cur in zip(rows, rows[1:], strict=False):
            if prev.to_state is not ReviewerState.CHANGES_REQUESTED:
                continue
            if cur.event != Event.FIXES_DONE:
                continue
            author = cur.review.author_user_id
            if author is None:
                continue  # nobody to attribute this to — see _sync_author
            label = cur.review.author_label or f"@{cur.review.author_username}"
            fix_samples[label].append(_working_time(calendar, prev.at, cur.at))

    return StatsReport(
        since=since,
        until=until,
        author_fix_time=_ranked(fix_samples),
        reviewer_response_time=_ranked(response_samples),
    )


def _working_time(calendar: WorkCalendar, begin: datetime, end: datetime) -> timedelta:
    return timedelta(seconds=calendar.working_seconds_between(begin, end))


def _ranked(samples: dict[str, list[timedelta]]) -> list[PersonStat]:
    stats = [
        PersonStat(label=label, sample_count=len(durations), durations=tuple(durations))
        for label, durations in samples.items()
    ]
    stats.sort(key=lambda s: s.median, reverse=True)
    return stats
