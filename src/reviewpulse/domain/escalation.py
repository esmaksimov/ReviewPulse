"""When, and why, a reviewer gets a DM.

Pure policy layer: it never touches the database or Telegram, it only answers
"given this assignment and this instant, should we nudge, and what for?".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .state import Assignment, ReviewerState
from .workhours import WorkCalendar


class NudgeReason(StrEnum):
    NO_REACTION = "no_reaction"
    """The reviewer never gave a verdict and the SLA has run out."""

    STALE_CHANGES_REQUESTED = "stale_changes_requested"
    """The author's fixes are in, ✍️ still stands, and the reviewer hasn't come back."""


class Nudge(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: NudgeReason
    overdue_by: timedelta
    """Working time elapsed since the SLA expired — used to phrase the reminder."""


class EscalationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    calendar: WorkCalendar
    sla: timedelta = Field(default=timedelta(hours=2), gt=timedelta(0))
    recheck_sla: timedelta = Field(default=timedelta(hours=2), gt=timedelta(0))
    interval: timedelta = Field(default=timedelta(minutes=20), gt=timedelta(0))
    max_per_day: int = Field(default=8, ge=1)

    def sla_for(self, state: ReviewerState) -> timedelta:
        return self.recheck_sla if state is ReviewerState.AWAITING_RECHECK else self.sla

    def deadline_for(self, assignment: Assignment) -> datetime:
        """Wall-clock moment at which this assignment becomes overdue."""
        return self.calendar.add_working_time(
            assignment.ball_since, self.sla_for(assignment.state)
        )

    def nudges_used_today(self, assignment: Assignment, now: datetime) -> int:
        """The stored counter, but only if it belongs to today's local date."""
        if assignment.last_nudge_at is None:
            return 0
        same_day = (
            self.calendar.to_local(assignment.last_nudge_at).date()
            == self.calendar.to_local(now).date()
        )
        return assignment.nudges_today if same_day else 0

    def evaluate(self, assignment: Assignment, now: datetime) -> Nudge | None:
        """Return the nudge due right now, or None if the reviewer should be left alone."""
        if not assignment.holds_ball:
            return None
        # Silence outside the work window is absolute — it outranks every other rule.
        if not self.calendar.is_working(now):
            return None
        if assignment.snoozed_until is not None and now < assignment.snoozed_until:
            return None
        if self.nudges_used_today(assignment, now) >= self.max_per_day:
            return None

        elapsed = timedelta(
            seconds=self.calendar.working_seconds_between(assignment.ball_since, now)
        )
        sla = self.sla_for(assignment.state)
        if elapsed < sla:
            return None

        if assignment.last_nudge_at is not None:
            since_last = timedelta(
                seconds=self.calendar.working_seconds_between(assignment.last_nudge_at, now)
            )
            if since_last < self.interval:
                return None

        reason = (
            NudgeReason.STALE_CHANGES_REQUESTED
            if assignment.state is ReviewerState.AWAITING_RECHECK
            else NudgeReason.NO_REACTION
        )
        return Nudge(reason=reason, overdue_by=elapsed - sla)


def policy_from_settings(settings, calendar: WorkCalendar) -> EscalationPolicy:  # noqa: ANN001
    return EscalationPolicy(
        calendar=calendar,
        sla=settings.sla,
        recheck_sla=settings.recheck_sla,
        interval=settings.nudge_interval,
        max_per_day=settings.max_nudges_per_day,
    )
