"""Working-time arithmetic.

Every deadline in ReviewPulse is measured in *working* seconds, not wall-clock ones.
A review posted at 17:30 on Friday is not "2 hours overdue" at 19:30 — its SLA has
consumed 30 minutes, and the remaining 90 tick away from 09:00 on Monday.

Pure module: no I/O, no globals, no now(). Everything is passed in, which is what
makes the escalation rules cheap to test.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Guard against an unbounded walk if a review is somehow left open for years.
_MAX_DAYS_SCAN = 400


class WorkCalendar(BaseModel):
    """A weekly working-hours window in a fixed UTC offset."""

    model_config = ConfigDict(frozen=True)

    tz_offset_hours: int = Field(default=3, ge=-12, le=14)
    start: time = time(9, 0)
    end: time = time(18, 0)
    #: 0 = Monday .. 6 = Sunday
    weekdays: frozenset[int] = Field(default=frozenset({0, 1, 2, 3, 4}), min_length=1)

    @model_validator(mode="after")
    def _check_window(self) -> WorkCalendar:
        if self.start >= self.end:
            raise ValueError("work window start must be before end (overnight shifts unsupported)")
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("weekdays must be 0 (Monday) .. 6 (Sunday)")
        return self

    # -- timezone helpers ---------------------------------------------------

    @property
    def tz(self) -> timezone:
        return timezone(timedelta(hours=self.tz_offset_hours))

    def to_local(self, moment: datetime) -> datetime:
        return _as_utc(moment).astimezone(self.tz)

    # -- calendar predicates ------------------------------------------------

    def is_working_day(self, day: date) -> bool:
        """Extension point: a public-holiday calendar would be consulted here too."""
        return day.weekday() in self.weekdays

    def is_working(self, moment: datetime) -> bool:
        local = self.to_local(moment)
        return self.is_working_day(local.date()) and self.start <= local.time() < self.end

    # -- core arithmetic ----------------------------------------------------

    def working_seconds_between(self, begin: datetime, end: datetime) -> float:
        """Working seconds contained in the half-open interval [begin, end)."""
        begin_local = self.to_local(begin)
        end_local = self.to_local(end)
        if end_local <= begin_local:
            return 0.0

        total = 0.0
        for window_start, window_end in self._windows_from(begin_local.date()):
            if window_start >= end_local:
                break
            overlap_start = max(window_start, begin_local)
            overlap_end = min(window_end, end_local)
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds()
        return total

    def add_working_time(self, begin: datetime, amount: timedelta) -> datetime:
        """The moment at which `amount` of working time has elapsed after `begin`.

        A zero or negative amount clamps `begin` forward to the next working moment,
        so the result is always a point inside the work window.
        """
        begin_local = self.to_local(begin)
        remaining = max(amount.total_seconds(), 0.0)

        for window_start, window_end in self._windows_from(begin_local.date()):
            if window_end <= begin_local:
                continue
            cursor = max(window_start, begin_local)
            available = (window_end - cursor).total_seconds()
            if remaining < available:
                return (cursor + timedelta(seconds=remaining)).astimezone(UTC)
            remaining -= available

        raise OverflowError(
            f"could not consume {amount} of working time within {_MAX_DAYS_SCAN} days"
        )

    def next_working_moment(self, moment: datetime) -> datetime:
        """`moment` itself if it is inside the window, otherwise the next opening bell."""
        return self.add_working_time(moment, timedelta(0))

    def next_window_start_after(self, moment: datetime) -> datetime:
        """The opening bell of the first working window that begins after `moment`.

        This is what "snooze until tomorrow" means: not a flat 24 hours, but the start
        of the next working day — which over a weekend is Monday morning.
        """
        local = self.to_local(moment)
        for window_start, _ in self._windows_from(local.date()):
            if window_start > local:
                return window_start.astimezone(UTC)
        raise OverflowError("no working window found ahead")

    # -- internals ----------------------------------------------------------

    def _windows_from(self, first_day: date) -> Iterator[tuple[datetime, datetime]]:
        """Yield (start, end) of each working window, in local time, from `first_day` on."""
        for offset in range(_MAX_DAYS_SCAN):
            day = first_day + timedelta(days=offset)
            if not self.is_working_day(day):
                continue
            yield (
                datetime.combine(day, self.start, tzinfo=self.tz),
                datetime.combine(day, self.end, tzinfo=self.tz),
            )


def _as_utc(moment: datetime) -> datetime:
    """Naive datetimes are treated as UTC — the bot stores UTC everywhere."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def calendar_from_settings(settings) -> WorkCalendar:  # noqa: ANN001 - avoids import cycle
    return WorkCalendar(
        tz_offset_hours=settings.timezone_offset_hours,
        start=settings.work_start,
        end=settings.work_end,
        weekdays=frozenset(settings.work_days),
    )
