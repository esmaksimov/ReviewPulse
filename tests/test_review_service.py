"""End-to-end over the real database: post -> verdicts -> nudges -> close."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from conftest import msk
from reviewpulse.db import repo
from reviewpulse.domain.escalation import EscalationPolicy, Nudge, NudgeReason
from reviewpulse.domain.state import Event, ReviewerState
from reviewpulse.domain.workhours import WorkCalendar
from reviewpulse.parsing.post_parser import parse_post
from reviewpulse.services import nudges as nudge_service
from reviewpulse.services import reviews as review_service

POST = """Платежи

Доработка connection pool

MR SC: https://git.example.com/backend/services/api_controller/-/merge_requests/1112

MR Utils: https://git.example.com/backend/packages/utils/-/merge_requests/223

Ревью: @user1 @user2"""


async def make_review(session, posted_at=None, text: str = POST):
    return await review_service.create_or_update_review(
        session,
        channel_chat_id=-1001234567890,
        channel_message_id=42,
        post=parse_post(text),
        raw_text=text,
        posted_at=posted_at or msk(27, 10, 0),
    )


@dataclass
class RecordingSender:
    sent: list[tuple[int, Nudge]]

    async def send(self, assignment, nudge: Nudge) -> bool:
        self.sent.append((assignment.id, nudge))
        return True


@pytest.fixture
def policy() -> EscalationPolicy:
    return EscalationPolicy(calendar=WorkCalendar())


async def link(session, username: str, telegram_user_id: int) -> None:
    await repo.upsert_user(session, telegram_user_id, username=username)
    await review_service.link_user_to_assignments(session, telegram_user_id, username)


async def test_post_becomes_a_review_with_reviewers_and_merge_requests(session) -> None:
    review = await make_review(session)

    assert review.product == "Платежи"
    assert len(review.merge_requests) == 2
    assert [row.username for row in review.assignments] == ["user1", "user2"]
    assert all(row.state is ReviewerState.PENDING for row in review.assignments)


async def test_a_github_pull_request_link_is_tracked_with_its_platform(session) -> None:
    text = POST + "\nPR: https://github.com/example-org/example-repo/pull/9"
    review = await make_review(session, text=text)

    assert {link.platform for link in review.merge_requests} == {"gitlab", "github"}
    github_link = next(link for link in review.merge_requests if link.platform == "github")
    assert github_link.iid == 9
    assert github_link.web_url == "https://github.com/example-org/example-repo/pull/9"


async def test_reprocessing_the_same_post_is_idempotent(session) -> None:
    """The channel post and its discussion-group copy both hit this path."""
    first = await make_review(session)
    await session.commit()
    second = await make_review(session)

    assert first.id == second.id
    assert len(second.assignments) == 2
    assert len(second.merge_requests) == 2


async def test_an_mr_less_review_with_labelled_reviewers_is_still_tracked(session) -> None:
    """A real post: an infra/docs-only change with no code MR, only a wiki link and
    an explicit "Ревьюверы:" line. The full create-and-nudge path must still work."""
    text = (
        "Продукт\n\nВынос телефона в отдельное хранилище\n\n"
        "Дока: https://wiki.example.com/pages/1\n\n"
        "Ревьюверы: @user1 @user2"
    )
    review = await make_review(session, text=text)

    assert review.merge_requests == []
    assert [row.username for row in review.assignments] == ["user1", "user2"]

    await link(session, "user1", 101)
    assignment = await repo.find_assignment(session, review.id, 101)
    result = await review_service.apply_verdict(session, assignment, Event.APPROVE)
    assert not result.review_closed, "the second reviewer still hasn't approved"


async def test_editing_a_post_preserves_verdicts_already_given(session) -> None:
    review = await make_review(session)
    await link(session, "user1", 101)
    assignment = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, assignment, Event.APPROVE)
    await session.commit()

    edited = POST.replace("Доработка connection pool", "Доработка connection pool (v2)")
    review = await make_review(session, text=edited)

    approved = [row for row in review.assignments if row.state is ReviewerState.APPROVED]
    assert len(approved) == 1, "a reworded post must not wipe an approval"
    assert review.title == "Доработка connection pool (v2)"


async def test_editing_a_post_drops_a_reviewer_no_longer_named(session) -> None:
    """The reviewer line is authoritative: dropping a handle from it must drop the
    assignment too, or a stale reviewer keeps propping up the approval count."""
    review = await make_review(session)
    assert {row.username for row in review.assignments} == {"user1", "user2"}

    edited = POST.replace("Ревью: @user1 @user2", "Ревью: @user2")
    review = await make_review(session, text=edited)

    active = [row for row in review.assignments if row.removed_at is None]
    assert {row.username for row in active} == {"user2"}
    dropped = next(row for row in review.assignments if row.username == "user1")
    assert dropped.removed_at is not None
    assert review_service.approvals_needed(review, cap=2) == 1


async def test_a_dropped_reviewers_stale_approval_does_not_count_toward_quorum(session) -> None:
    """The exact bug this guards against: user1 approves, the post is then edited to
    swap them for user3 — the review must not close on user1's now-stale 👍 plus a
    single approval from one of the two people actually named."""
    review = await make_review(session)
    await link(session, "user1", 101)
    await link(session, "user2", 102)

    first = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, first, Event.APPROVE)
    await session.commit()

    edited = POST.replace("Ревью: @user1 @user2", "Ревью: @user2 @user3")
    review = await make_review(session, text=edited)
    await link(session, "user3", 103)

    assert review.approvals == 0, "user1's approval must not count once they're dropped"
    assert review_service.approvals_needed(review, cap=2) == 2

    second = await repo.find_assignment(session, review.id, 102)
    result = await review_service.apply_verdict(session, second, Event.APPROVE)
    assert not result.review_closed, "only one of the two real reviewers has approved"

    third = await repo.find_assignment(session, review.id, 103)
    result = await review_service.apply_verdict(session, third, Event.APPROVE)
    assert result.review_closed


async def test_re_adding_a_dropped_reviewer_clears_the_drop(session) -> None:
    review = await make_review(session)

    without_user2 = POST.replace("Ревью: @user1 @user2", "Ревью: @user1")
    review = await make_review(session, text=without_user2)
    dropped = next(row for row in review.assignments if row.username == "user2")
    assert dropped.removed_at is not None

    review = await make_review(session, text=POST)
    restored = next(row for row in review.assignments if row.username == "user2")
    assert restored.removed_at is None
    assert restored.state is ReviewerState.PENDING


async def test_an_edit_with_no_reviewer_line_never_drops_anyone(session) -> None:
    """Without an explicit reviewer-labelled line, `post.reviewers` only reflects
    whatever @handles happen to appear in the text — too noisy to treat as the
    authoritative reviewer list. A reword that drops the label entirely must not
    silently wipe out every assignment."""
    review = await make_review(session)

    edited = POST.replace("Ревью: @user1 @user2", "см. обсуждение с @user1 @user2")
    review = await make_review(session, text=edited)

    assert all(row.removed_at is None for row in review.assignments)


async def test_an_author_line_resolves_immediately_when_the_handle_is_already_known(
    session,
) -> None:
    """Posting the author is opt-in — see `parsing.post_parser._find_author` — and
    resolves the same way a reviewer's @handle does."""
    await repo.upsert_user(session, 555, username="alice")
    review = await make_review(session, text=POST + "\nАвтор: @alice")

    assert review.author_user_id == 555
    assert review.author_username == "alice"


async def test_an_author_lines_unknown_handle_is_backfilled_on_start(session) -> None:
    review = await make_review(session, text=POST + "\nАвтор: @alice")
    assert review.author_user_id is None
    assert review.author_username == "alice"

    await repo.upsert_user(session, 555, username="alice")
    linked = await review_service.link_author_to_reviews(session, 555, "alice")

    assert linked == 1
    assert review.author_user_id == 555


async def test_editing_the_author_line_reassigns_authorship(session) -> None:
    await repo.upsert_user(session, 555, username="alice")
    await repo.upsert_user(session, 777, username="bobby")
    review = await make_review(session, text=POST + "\nАвтор: @alice")
    assert review.author_user_id == 555

    edited = (POST + "\nАвтор: @bobby").replace(
        "Доработка connection pool", "Доработка connection pool (v2)"
    )
    review = await make_review(session, text=edited)

    assert review.author_user_id == 777
    assert review.author_username == "bobby"


async def test_without_an_author_line_no_author_is_tracked(session) -> None:
    review = await make_review(session)
    assert review.author_user_id is None
    assert review.author_username is None


async def test_the_author_reviewing_their_own_post_is_not_added_as_a_reviewer(session) -> None:
    """Mirrors the existing user_id-based self-review skip, but keyed off the
    @handle: the author's own telegram id is not known yet at post time."""
    text = POST.replace("Ревью: @user1 @user2", "Ревью: @user1 @user2 @alice") + "\nАвтор: @alice"
    review = await make_review(session, text=text)

    assert {row.username for row in review.assignments} == {"user1", "user2"}


async def test_reviews_awaiting_author_lists_only_open_changes_requested(session) -> None:
    await repo.upsert_user(session, 555, username="alice")
    review = await make_review(session, text=POST + "\nАвтор: @alice")
    await link(session, "user1", 101)
    await link(session, "user2", 102)

    assert await repo.reviews_awaiting_author(session, 555) == []

    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES)

    waiting = await repo.reviews_awaiting_author(session, 555)
    assert [item.id for item in waiting] == [review.id]

    await review_service.mark_fixes_done(session, review)
    assert await repo.reviews_awaiting_author(session, 555) == []


async def test_two_approvals_close_the_review(session) -> None:
    review = await make_review(session)
    await link(session, "user1", 101)
    await link(session, "user2", 102)

    first = await repo.find_assignment(session, review.id, 101)
    result = await review_service.apply_verdict(session, first, Event.APPROVE)
    assert not result.review_closed

    second = await repo.find_assignment(session, review.id, 102)
    result = await review_service.apply_verdict(session, second, Event.APPROVE)
    assert result.review_closed
    assert review.is_closed


async def test_a_single_named_reviewer_alone_closes_the_review(session) -> None:
    """Name one reviewer and their approval is enough — don't wait on a second
    verdict that was never coming."""
    review = await make_review(session, text=POST.replace("Ревью: @user1 @user2", "Ревью: @user1"))
    await link(session, "user1", 101)

    assignment = await repo.find_assignment(session, review.id, 101)
    result = await review_service.apply_verdict(session, assignment, Event.APPROVE)

    assert result.review_closed
    assert review.is_closed


async def test_the_cap_limits_approvals_needed_for_a_long_reviewer_list(session) -> None:
    """Name three reviewers with the default cap of 2 and only two approvals close
    it — naming a long list doesn't turn into a unanimous-approval requirement."""
    text = POST.replace("Ревью: @user1 @user2", "Ревью: @user1 @user2 @user3")
    review = await make_review(session, text=text)
    for username, uid in (("user1", 101), ("user2", 102), ("user3", 103)):
        await link(session, username, uid)

    assert review_service.approvals_needed(review, cap=2) == 2

    first = await repo.find_assignment(session, review.id, 101)
    result = await review_service.apply_verdict(session, first, Event.APPROVE, approvals_cap=2)
    assert not result.review_closed

    second = await repo.find_assignment(session, review.id, 102)
    result = await review_service.apply_verdict(session, second, Event.APPROVE, approvals_cap=2)
    assert result.review_closed, "the third reviewer's verdict was never required"


async def test_pressing_the_same_button_twice_is_a_no_op(session) -> None:
    review = await make_review(session)
    await link(session, "user1", 101)
    assignment = await repo.find_assignment(session, review.id, 101)

    assert (await review_service.apply_verdict(session, assignment, Event.APPROVE)).changed
    assert not (await review_service.apply_verdict(session, assignment, Event.APPROVE)).changed


async def test_start_backfills_the_telegram_id_onto_existing_assignments(session) -> None:
    """Reviewers are named by @handle; a DM needs the numeric id."""
    review = await make_review(session)
    assert all(row.telegram_user_id is None for row in review.assignments)

    await repo.upsert_user(session, 101, username="user1")
    linked = await review_service.link_user_to_assignments(session, 101, "user1")

    assert linked == 1
    assignment = await repo.find_assignment(session, review.id, 101)
    assert assignment is not None


async def test_a_known_user_is_linked_at_post_time(session) -> None:
    await repo.upsert_user(session, 101, username="user1")
    review = await make_review(session)

    linked = [row for row in review.assignments if row.telegram_user_id == 101]
    assert len(linked) == 1


# --- the nudge loop ---------------------------------------------------------


async def test_no_reminder_before_the_sla(session, policy) -> None:
    await make_review(session, posted_at=msk(27, 10, 0))
    await link(session, "user1", 101)
    sender = RecordingSender(sent=[])

    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 11, 0))
    assert sender.sent == []


async def test_reminds_a_silent_reviewer_after_the_sla(session, policy) -> None:
    await make_review(session, posted_at=msk(27, 10, 0))
    await link(session, "user1", 101)
    sender = RecordingSender(sent=[])

    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 12, 30))

    assert len(sender.sent) == 1
    assert sender.sent[0][1].reason is NudgeReason.NO_REACTION


async def test_does_not_repeat_within_the_interval(session, policy) -> None:
    await make_review(session, posted_at=msk(27, 10, 0))
    await link(session, "user1", 101)
    sender = RecordingSender(sent=[])

    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 12, 30))
    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 12, 40))
    assert len(sender.sent) == 1

    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 12, 55))
    assert len(sender.sent) == 2


async def test_stays_silent_outside_working_hours(session, policy) -> None:
    await make_review(session, posted_at=msk(27, 10, 0))
    await link(session, "user1", 101)
    sender = RecordingSender(sent=[])

    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(28, 3, 0))
    assert sender.sent == []


async def test_reviewer_waiting_on_the_author_is_not_reminded(session, policy) -> None:
    review = await make_review(session, posted_at=msk(27, 10, 0))
    await link(session, "user1", 101)
    assignment = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, assignment, Event.REQUEST_CHANGES, msk(27, 10, 30))

    sender = RecordingSender(sent=[])
    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(29, 15, 0))
    assert sender.sent == []


async def test_the_whole_stale_changes_requested_cycle(session, policy) -> None:
    """The scenario the bot exists for, start to finish."""
    review = await make_review(session, posted_at=msk(27, 9, 0))
    await link(session, "user1", 101)
    await link(session, "user2", 102)
    sender = RecordingSender(sent=[])

    # Reviewer asks for changes; the ball is on the author, so no reminders.
    reviewer = await repo.find_assignment(session, review.id, 101)
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES, msk(27, 9, 30))
    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 16, 0))
    assert [item for item in sender.sent if item[0] == reviewer.id] == []

    # Author reports the fixes; the ball goes back and the recheck clock starts.
    moved = await review_service.mark_fixes_done(session, review, at=msk(27, 10, 0))
    assert [row.id for row in moved] == [reviewer.id]
    assert reviewer.state is ReviewerState.AWAITING_RECHECK

    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 11, 0))
    assert [item for item in sender.sent if item[0] == reviewer.id] == [], "recheck SLA not up"

    stale = await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 12, 30))
    reasons = [nudge.reason for assignment, nudge in stale if assignment.id == reviewer.id]
    assert reasons == [NudgeReason.STALE_CHANGES_REQUESTED]

    # The reviewer comes back and asks for more — reminders must stop dead.
    await review_service.apply_verdict(session, reviewer, Event.REQUEST_CHANGES, msk(27, 13, 0))
    before = len(sender.sent)
    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 17, 0))
    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(29, 12, 0))
    assert [item for item in sender.sent[before:] if item[0] == reviewer.id] == []

    # Second round of fixes puts the ball back once more.
    await review_service.mark_fixes_done(session, review, at=msk(29, 12, 30))
    assert reviewer.state is ReviewerState.AWAITING_RECHECK
    again = await nudge_service.run_nudge_tick(session, policy, sender, now=msk(29, 15, 0))
    assert any(assignment.id == reviewer.id for assignment, _ in again)

    # And an approval ends it.
    await review_service.apply_verdict(session, reviewer, Event.APPROVE, msk(29, 15, 30))
    after = len(sender.sent)
    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(29, 17, 0))
    assert [item for item in sender.sent[after:] if item[0] == reviewer.id] == []


async def test_closing_the_review_silences_everyone(session, policy) -> None:
    review = await make_review(session, posted_at=msk(27, 9, 0))
    await link(session, "user1", 101)
    await link(session, "user2", 102)

    await review_service.close_review(session, review, at=msk(27, 10, 0))

    sender = RecordingSender(sent=[])
    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 15, 0))
    assert sender.sent == []


async def test_unreachable_reviewer_is_skipped(session, policy) -> None:
    """No telegram id means no DM is possible — the loop must not trip over it."""
    await make_review(session, posted_at=msk(27, 9, 0))
    sender = RecordingSender(sent=[])

    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 15, 0))
    assert sender.sent == []


async def test_muted_user_is_not_reminded(session, policy) -> None:
    await make_review(session, posted_at=msk(27, 9, 0))
    await link(session, "user1", 101)
    user = await repo.get_user_by_telegram_id(session, 101)
    user.muted_until = msk(27, 18, 0)
    await session.flush()

    sender = RecordingSender(sent=[])
    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 15, 0))
    assert sender.sent == []

    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(28, 15, 0))
    assert len(sender.sent) == 1, "the mute expires"


async def test_blocked_user_is_excluded_from_the_query(session, policy) -> None:
    await make_review(session, posted_at=msk(27, 9, 0))
    await link(session, "user1", 101)
    user = await repo.get_user_by_telegram_id(session, 101)
    user.can_be_dmed = False
    await session.flush()

    sender = RecordingSender(sent=[])
    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 15, 0))
    assert sender.sent == []


async def test_daily_budget_is_enforced_across_ticks(session, policy) -> None:
    await make_review(session, posted_at=msk(27, 9, 0))
    await link(session, "user1", 101)
    sender = RecordingSender(sent=[])

    minute = 0
    for _ in range(20):
        minute += 21
        moment = msk(27, 11 + minute // 60, minute % 60)
        if moment.hour >= 18:
            break
        await nudge_service.run_nudge_tick(session, policy, sender, now=moment)

    assert len(sender.sent) <= policy.max_per_day


async def test_a_failing_send_does_not_stall_the_tick(session, policy) -> None:
    review = await make_review(session, posted_at=msk(27, 9, 0))
    await link(session, "user1", 101)
    await link(session, "user2", 102)

    class HalfBrokenSender:
        def __init__(self) -> None:
            self.sent: list[int] = []

        async def send(self, assignment, nudge) -> bool:
            if assignment.telegram_user_id == 101:
                raise RuntimeError("chat not found")
            self.sent.append(assignment.telegram_user_id)
            return True

    sender = HalfBrokenSender()
    await nudge_service.run_nudge_tick(session, policy, sender, now=msk(27, 15, 0))

    assert sender.sent == [102], "the second reviewer still gets their reminder"
    assert review.id is not None


async def test_a_failed_send_is_logged_but_does_not_burn_the_budget(session, policy) -> None:
    await make_review(session, posted_at=msk(27, 9, 0))
    await link(session, "user1", 101)

    class DeadSender:
        async def send(self, assignment, nudge) -> bool:
            return False

    await nudge_service.run_nudge_tick(session, policy, DeadSender(), now=msk(27, 15, 0))

    assignment = (await repo.nudgeable_assignments(session, msk(27, 15, 0)))[0]
    assert assignment.last_nudge_at is None
    assert assignment.nudges_today == 0
