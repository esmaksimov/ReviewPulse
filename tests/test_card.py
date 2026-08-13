"""Pure card-rendering helpers: no DB session needed, just a Review instance."""

from __future__ import annotations

from reviewpulse.db.models import MergeRequestLink, Review
from reviewpulse.telegram import card


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
