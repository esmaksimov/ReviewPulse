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


async def test_assignment_transitions_are_recorded_and_survive_a_restart(tmp_path) -> None:
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

        from reviewpulse.domain.state import Event

        await review_service.apply_verdict(session, assignment, Event.APPROVE, msk(27, 11, 0))
    await database.dispose()

    reopened = Database(url)
    async with reopened.session() as session:
        rows = await repo.transitions_between(session, msk(27, 0, 0), msk(28, 0, 0))
        assert len(rows) == 1
        assert rows[0].event == "approve"
        assert rows[0].to_state.value == "approved"
        assert rows[0].at == msk(27, 11, 0)
        # Relationships resolve after a fresh load, not just within the writing session.
        assert rows[0].review.id == review.id
        assert rows[0].assignment.id == assignment.id
    await reopened.dispose()


async def test_announcement_draft_survives_a_restart(tmp_path) -> None:
    from reviewpulse.parsing.gitlab_url import MergeRequestRef

    url = f"sqlite+aiosqlite:///{tmp_path / 'reviewpulse.db'}"

    database = Database(url)
    await database.create_all()
    async with database.session() as session:
        draft = await repo.create_draft(
            session,
            composer_user_id=1,
            composer_username="author",
            chat_id=1,
            project_path="backend/api",
            product="Demo",
            title="Title",
            task_url=None,
            docs_url=None,
            merge_requests=[
                MergeRequestRef(host="git.example.com", project_path="backend/api", iid=1)
            ],
            techlead_username="lead",
            pool_pick_usernames=["p1"],
        )
        draft_id = draft.id
        await repo.set_draft_preview_message(session, draft, 555)
    await database.dispose()

    reopened = Database(url)
    async with reopened.session() as session:
        draft = await repo.get_draft(session, draft_id)
        assert draft is not None
        assert draft.preview_message_id == 555
        assert repo.draft_pool_picks(draft) == ["p1"]
        assert [ref.iid for ref in repo.draft_merge_requests(draft)] == [1]
        assert draft.published_at is None

        await repo.mark_draft_published(session, draft)
        assert draft.published_at is not None
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
