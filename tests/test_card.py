"""Pure card-rendering helpers: no DB session needed, just a Review instance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage

from reviewpulse.db.models import MergeRequestLink, Review, ReviewerAssignment
from reviewpulse.domain.state import ReviewerState
from reviewpulse.telegram import card


@dataclass
class FakeBot:
    """Records `delete_message` calls; `fail_message` makes it raise like Telegram
    does — with whatever error text the test needs, e.g. missing rights vs. the post
    already being gone."""

    fail_message: str | None = None
    deleted: list[tuple[int, int]] = field(default_factory=list)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        if self.fail_message is not None:
            raise TelegramBadRequest(
                method=DeleteMessage(chat_id=chat_id, message_id=message_id),
                message=self.fail_message,
            )
        self.deleted.append((chat_id, message_id))


def test_review_url_points_at_the_discussion_thread() -> None:
    """This is the link every DM's "Open discussion" button uses — and now what
    /status links each review's headline to as well."""
    review = Review(
        channel_chat_id=-1001111111111,
        channel_message_id=1,
        discussion_chat_id=-1002222222222,
        discussion_message_id=456,
    )
    assert card.review_url(review) == "https://t.me/c/2222222222/456"


def test_review_url_is_none_before_the_discussion_copy_is_linked() -> None:
    """The channel post and its auto-forwarded copy can arrive in either order —
    until the copy lands, there is nowhere to link to."""
    review = Review(channel_chat_id=-1001111111111, channel_message_id=1)
    assert card.review_url(review) is None


def test_headline_falls_back_to_the_localized_default_when_untitled() -> None:
    review = Review(channel_chat_id=-1, channel_message_id=1)
    assert card.headline(review, "en") == "Review"
    assert card.headline(review, "ru") == "Ревью"


def test_headline_joins_product_and_title() -> None:
    review = Review(
        channel_chat_id=-1, channel_message_id=1, product="Payments", title="Fix redirect"
    )
    assert card.headline(review, "en") == "Payments — Fix redirect"


def test_merge_request_pairs_label_short_project_and_iid() -> None:
    review = Review(channel_chat_id=-1, channel_message_id=1)
    review.merge_requests = [
        MergeRequestLink(host="git.example.com", project_path="backend/api", iid=42)
    ]
    assert card.merge_request_pairs(review) == [
        ("api!42", "https://git.example.com/backend/api/-/merge_requests/42")
    ]


def test_merge_request_pairs_uses_githubs_own_convention_for_a_github_link() -> None:
    review = Review(channel_chat_id=-1, channel_message_id=1)
    review.merge_requests = [
        MergeRequestLink(
            host="github.com", project_path="example-org/example-repo", iid=9, platform="github"
        )
    ]
    assert card.merge_request_pairs(review) == [
        ("example-repo#9", "https://github.com/example-org/example-repo/pull/9")
    ]


def test_render_drops_a_reviewer_removed_by_a_later_edit() -> None:
    """A dropped reviewer (see services.reviews._sync_assignments) must vanish from
    the card, not just stop counting toward quorum."""
    review = Review(
        id=1,
        channel_chat_id=-1,
        channel_message_id=1,
        discussion_chat_id=-2,
        discussion_message_id=1,
        author_user_id=555,
    )
    review.assignments = [
        ReviewerAssignment(
            mention_key="un:user1", display_label="@user1", state=ReviewerState.PENDING
        ),
        ReviewerAssignment(
            mention_key="un:user2",
            display_label="@user2",
            state=ReviewerState.PENDING,
            removed_at=datetime(2026, 8, 19, tzinfo=UTC),
        ),
    ]

    text, _ = card.render(review, approvals_cap=2, locale="en")
    assert "@user1" in text
    assert "@user2" not in text


async def test_delete_from_channel_removes_the_original_post() -> None:
    review = Review(id=1, channel_chat_id=-1001111111111, channel_message_id=42)
    bot = FakeBot()

    result = await card.delete_from_channel(bot, review)

    assert result is True
    assert bot.deleted == [(-1001111111111, 42)]


async def test_delete_from_channel_is_a_noop_before_the_channel_message_is_known() -> None:
    """Shouldn't happen for a real review, but a bare `Review()` in a test or a
    not-yet-flushed row must not crash trying to delete "message None"."""
    review = Review(id=1, channel_chat_id=None, channel_message_id=None)
    bot = FakeBot()

    result = await card.delete_from_channel(bot, review)

    assert result is False
    assert bot.deleted == []


async def test_delete_from_channel_reports_failure_when_the_bot_lacks_the_right() -> None:
    """Without the "Delete messages" admin right, Telegram refuses — the caller
    (`scheduler.jobs.channel_cleanup_tick`) needs `False` back to know to retry on
    the next tick rather than mark this one done."""
    review = Review(id=1, channel_chat_id=-1001111111111, channel_message_id=42)
    bot = FakeBot(fail_message="not enough rights to delete a message")

    result = await card.delete_from_channel(bot, review)  # must not raise

    assert result is False


async def test_delete_from_channel_treats_an_already_gone_post_as_done() -> None:
    """A double-run, or someone deleting the post by hand — either way the post is
    already gone, which is the end state this function exists to reach, so it must
    not be retried forever."""
    review = Review(id=1, channel_chat_id=-1001111111111, channel_message_id=42)
    bot = FakeBot(fail_message="message to delete not found")

    result = await card.delete_from_channel(bot, review)

    assert result is True
