"""Regression cover for state that has to survive a restart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from conftest import msk
from reviewpulse.db import repo
from reviewpulse.db.session import Database
from reviewpulse.domain.escalation import EscalationPolicy
from reviewpulse.domain.workhours import WorkCalendar
from reviewpulse.parsing.post_parser import parse_post
from reviewpulse.services import reviews as review_service

POST = (
    "Платежи\n\nФикс\n\n"
    "MR: https://git.example.com/backend/services/api/-/merge_requests/1\n\n"
    "Ревью: @user1"
)


async def test_timestamps_keep_their_timezone_across_a_restart(tmp_path) -> None:
    """SQLite stores no offset, so a naive read-back would shift every SLA by hours."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'reviewpulse.db'}"

    database = Database(url)
    await database.create_all()
    async with database.session() as session:
        await review_service.create_or_update_review(
            session,
            channel_chat_id=-100,
            channel_message_id=1,
            post=parse_post(POST),
            raw_text=POST,
            posted_at=msk(27, 10, 0),
        )
    await database.dispose()

    reopened = Database(url)  # a fresh process would do exactly this
    async with reopened.session() as session:
        review = (await repo.open_reviews(session))[0]
        ball_since = review.assignments[0].ball_since

        assert ball_since.tzinfo is not None, "a naive timestamp would be read as UTC"
        assert ball_since == msk(27, 10, 0)
        assert ball_since.astimezone(UTC) == datetime(
            2026, 7, 27, 7, 0, tzinfo=UTC
        )

        # And the deadline computed after the restart still lands where it should.
        policy = EscalationPolicy(calendar=WorkCalendar())
        deadline = policy.deadline_for(repo.to_domain(review.assignments[0]))
        assert deadline == msk(27, 12, 0)
    await reopened.dispose()


async def test_review_state_survives_a_restart(tmp_path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'reviewpulse.db'}"

    database = Database(url)
    await database.create_all()
    async with database.session() as session:
        review = await review_service.create_or_update_review(
            session,
            channel_chat_id=-100,
            channel_message_id=1,
            post=parse_post(POST),
            raw_text=POST,
            posted_at=msk(27, 10, 0),
        )
        await repo.upsert_user(session, 101, username="user1")
        await review_service.link_user_to_assignments(session, 101, "user1")
        await review_service.mark_fixes_done(session, review)
        assignment = await repo.find_assignment(session, review.id, 101)
        from reviewpulse.domain.state import Event

        await review_service.apply_verdict(
            session, assignment, Event.REQUEST_CHANGES, msk(27, 11, 0)
        )
    await database.dispose()

    reopened = Database(url)
    async with reopened.session() as session:
        assignment = (await repo.assignments_for_user(session, 101))[0]
        assert assignment.state.value == "changes_requested"
        assert assignment.ball_since == msk(27, 11, 0)
        assert assignment.review.merge_requests[0].iid == 1
    await reopened.dispose()


async def test_nudge_budget_is_not_reset_by_a_restart(tmp_path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'reviewpulse.db'}"

    database = Database(url)
    await database.create_all()
    async with database.session() as session:
        review = await review_service.create_or_update_review(
            session,
            channel_chat_id=-100,
            channel_message_id=1,
            post=parse_post(POST),
            raw_text=POST,
            posted_at=msk(27, 9, 0),
        )
        await repo.upsert_user(session, 101, username="user1")
        await review_service.link_user_to_assignments(session, 101, "user1")
        assignment = await repo.find_assignment(session, review.id, 101)
        await repo.record_nudge(
            session, assignment, reason="no_reaction", sent_at=msk(27, 12, 0), same_day=False
        )
    await database.dispose()

    reopened = Database(url)
    async with reopened.session() as session:
        assignment = (await repo.nudgeable_assignments(session, msk(27, 12, 10)))[0]
        assert assignment.nudges_today == 1
        assert assignment.last_nudge_at == msk(27, 12, 0)

        policy = EscalationPolicy(calendar=WorkCalendar())
        assert policy.evaluate(repo.to_domain(assignment), msk(27, 12, 10)) is None
        assert policy.evaluate(repo.to_domain(assignment), msk(27, 12, 25)) is not None
    await reopened.dispose()


def test_naive_timestamps_are_normalised_on_write() -> None:
    """Telegram hands us aware datetimes, but nothing else should be able to poison the
    column with a naive one."""
    from reviewpulse.db.models import UtcDateTime

    column = UtcDateTime()
    naive = datetime(2026, 7, 27, 7, 0)
    assert column.process_bind_param(naive, None) == datetime(
        2026, 7, 27, 7, 0, tzinfo=UTC
    )
    aware = datetime(2026, 7, 27, 10, 0, tzinfo=timezone(timedelta(hours=3)))
    assert column.process_bind_param(aware, None) == datetime(
        2026, 7, 27, 7, 0, tzinfo=UTC
    )
    assert column.process_bind_param(None, None) is None
    assert column.process_result_value(None, None) is None
