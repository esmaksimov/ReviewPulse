from datetime import datetime, timedelta, timezone

import pytest

from reviewpulse.domain.workhours import WorkCalendar

MSK = timezone(timedelta(hours=3))


@pytest.fixture
def calendar() -> WorkCalendar:
    return WorkCalendar()  # 09:00-18:00 UTC+3, Mon-Fri


def msk(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=MSK)


# 2026-07-27 is a Monday; 2026-07-31 a Friday; 2026-08-01/02 the weekend.


def test_inside_and_outside_the_window(calendar: WorkCalendar) -> None:
    assert calendar.is_working(msk(2026, 7, 27, 9, 0))
    assert calendar.is_working(msk(2026, 7, 27, 17, 59))
    assert not calendar.is_working(msk(2026, 7, 27, 8, 59))
    assert not calendar.is_working(msk(2026, 7, 27, 18, 0)), "end of window is exclusive"
    assert not calendar.is_working(msk(2026, 7, 27, 3, 0)), "nobody gets pinged at night"
    assert not calendar.is_working(msk(2026, 8, 1, 12, 0)), "Saturday"


def test_night_and_weekend_contribute_no_working_time(calendar: WorkCalendar) -> None:
    # Friday 17:30 -> Monday 09:30 is 64 wall-clock hours but only 1 working hour.
    elapsed = calendar.working_seconds_between(
        msk(2026, 7, 31, 17, 30), msk(2026, 8, 3, 9, 30)
    )
    assert elapsed == timedelta(hours=1).total_seconds()


def test_full_working_day_is_nine_hours(calendar: WorkCalendar) -> None:
    elapsed = calendar.working_seconds_between(
        msk(2026, 7, 27, 0, 0), msk(2026, 7, 28, 0, 0)
    )
    assert elapsed == timedelta(hours=9).total_seconds()


def test_interval_is_never_negative(calendar: WorkCalendar) -> None:
    assert calendar.working_seconds_between(
        msk(2026, 7, 27, 15, 0), msk(2026, 7, 27, 11, 0)
    ) == 0.0


def test_sla_started_late_in_the_day_lands_next_morning(calendar: WorkCalendar) -> None:
    """The headline case from the plan: posted 17:30, 2h SLA, expires 10:30 next day."""
    deadline = calendar.add_working_time(msk(2026, 7, 27, 17, 30), timedelta(hours=2))
    assert deadline.astimezone(MSK) == msk(2026, 7, 28, 10, 30)


def test_sla_started_on_friday_evening_lands_on_monday(calendar: WorkCalendar) -> None:
    deadline = calendar.add_working_time(msk(2026, 7, 31, 17, 30), timedelta(hours=2))
    assert deadline.astimezone(MSK) == msk(2026, 8, 3, 10, 30)


def test_sla_started_before_opening_starts_at_opening(calendar: WorkCalendar) -> None:
    deadline = calendar.add_working_time(msk(2026, 7, 27, 6, 0), timedelta(hours=2))
    assert deadline.astimezone(MSK) == msk(2026, 7, 27, 11, 0)


def test_add_and_measure_are_inverses(calendar: WorkCalendar) -> None:
    start = msk(2026, 7, 30, 16, 45)
    deadline = calendar.add_working_time(start, timedelta(hours=5))
    assert calendar.working_seconds_between(start, deadline) == pytest.approx(5 * 3600)


def test_next_working_moment_clamps_forward(calendar: WorkCalendar) -> None:
    assert calendar.next_working_moment(msk(2026, 8, 1, 12, 0)).astimezone(MSK) == msk(
        2026, 8, 3, 9, 0
    ), "Saturday noon -> Monday opening"
    inside = msk(2026, 7, 27, 12, 0)
    assert calendar.next_working_moment(inside).astimezone(MSK) == inside


def test_naive_datetimes_are_treated_as_utc(calendar: WorkCalendar) -> None:
    naive = datetime(2026, 7, 27, 9, 0)  # 12:00 MSK
    assert calendar.is_working(naive)
    assert calendar.to_local(naive).hour == 12


def test_custom_calendar_can_run_all_week(calendar: WorkCalendar) -> None:
    """The knobs used to fast-forward a manual end-to-end run."""
    always = WorkCalendar(weekdays=frozenset(range(7)))
    assert always.is_working(msk(2026, 8, 1, 12, 0))


def test_rejects_inverted_window() -> None:
    from datetime import time

    with pytest.raises(ValueError):
        WorkCalendar(start=time(18, 0), end=time(9, 0))
