"""/myposts: reassigning a reviewer on an already-published, still-open post.

Driven directly the same way `test_admin_flow.py` drives the admin handlers — no
aiogram dispatch harness needed, these are all stateless one-shot button taps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from conftest import msk
from reviewpulse.config import Settings
from reviewpulse.parsing.post_parser import parse_post
from reviewpulse.services import admin as admin_service
from reviewpulse.services import reviews as review_service
from reviewpulse.telegram import texts
from reviewpulse.telegram.handlers import myposts
from reviewpulse.telegram.keyboards import MyPostReplaceStart, MyPostReplaceWith, MyPostSelect

POST = """Demo Product

Connection pool rework

MR: https://git.example.com/backend/api/-/merge_requests/1

Ревью: @user1 @user2
Автор: @alice"""

SETTINGS = Settings(
    _env_file=None,
    BOT_TOKEN="t",
    DEFAULT_LOCALE="ru",
    REVIEW_PROJECTS={"backend/api": {"product": "Demo Product", "techlead": "lead"}},
)

AUTHOR = SimpleNamespace(id=1, username="alice", full_name="Alice", language_code="ru")
OTHER = SimpleNamespace(id=2, username="other", full_name="Other", language_code="ru")


async def make_review(session, text: str = POST):
    return await review_service.create_or_update_review(
        session,
        channel_chat_id=-1001234567890,
        channel_message_id=42,
        post=parse_post(text),
        raw_text=text,
        posted_at=msk(27, 10, 0),
    )


@dataclass
class Chat:
    sent: list = field(default_factory=list)

    def last_text(self) -> str:
        return self.sent[-1].text

    def last_markup(self):
        return self.sent[-1].markup


class FakeBot:
    async def edit_message_text(self, **kwargs):
        pass


class FakeMessage:
    def __init__(self, chat: Chat, user=AUTHOR, text=None, message_id=1):
        self._chat = chat
        self.text = text
        self.from_user = user
        self.chat = SimpleNamespace(id=555, type="private")
        self.message_id = message_id

    async def answer(self, text, reply_markup=None, **kwargs):
        self._chat.sent.append(SimpleNamespace(text=text, markup=reply_markup))
        return FakeMessage(self._chat, user=self.from_user)


class FakeQuery:
    def __init__(self, message: FakeMessage, user=AUTHOR):
        self.message = message
        self.from_user = user
        self.answers: list = []
        self.alerts: list = []

    async def answer(self, text=None, show_alert=False):
        (self.alerts if show_alert else self.answers).append(text)


def button_labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


# --- the listing ------------------------------------------------------------------


async def test_myposts_lists_only_open_reviews_authored_by_the_requester(session) -> None:
    await make_review(session)
    chat = Chat()
    await myposts.on_myposts(FakeMessage(chat, user=AUTHOR), session, SETTINGS)
    assert "Connection pool rework" in chat.last_text()


async def test_myposts_is_empty_for_someone_with_no_authored_reviews(session) -> None:
    await make_review(session)
    chat = Chat()
    await myposts.on_myposts(FakeMessage(chat, user=OTHER), session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "myposts_empty")


async def test_myposts_asks_for_a_username_when_the_requester_has_none(session) -> None:
    no_username = SimpleNamespace(id=3, username=None, full_name="Nobody", language_code="ru")
    chat = Chat()
    await myposts.on_myposts(FakeMessage(chat, user=no_username), session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "myposts_no_username")


async def test_myposts_excludes_a_closed_review(session) -> None:
    review = await make_review(session)
    await review_service.close_review(session, review)
    chat = Chat()
    await myposts.on_myposts(FakeMessage(chat, user=AUTHOR), session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "myposts_empty")


# --- selecting a review, then a reviewer to replace --------------------------------


async def test_select_lists_the_active_reviewers(session) -> None:
    review = await make_review(session)
    chat = Chat()
    await myposts.on_myposts_select(
        FakeQuery(FakeMessage(chat)), MyPostSelect(review_id=review.id), session, SETTINGS
    )
    labels = button_labels(chat.last_markup())
    assert any("user1" in label for label in labels)
    assert any("user2" in label for label in labels)


async def test_select_is_refused_to_someone_who_is_not_the_author(session) -> None:
    review = await make_review(session)
    query = FakeQuery(FakeMessage(Chat(), user=OTHER), user=OTHER)
    await myposts.on_myposts_select(
        query, MyPostSelect(review_id=review.id), session, SETTINGS
    )
    assert query.alerts == [texts.t("ru", "myposts_gone")]


# --- picking a replacement ----------------------------------------------------------


async def test_replace_start_offers_the_products_pool_minus_current_reviewers(
    session,
) -> None:
    review = await make_review(session)
    await admin_service.add_to_pool(
        session, username="user1", product=None, added_by_user_id=1
    )
    await admin_service.add_to_pool(
        session, username="user3", product=None, added_by_user_id=1
    )
    assignment = next(a for a in review.assignments if a.username == "user1")

    chat = Chat()
    await myposts.on_myposts_replace_start(
        FakeQuery(FakeMessage(chat)),
        MyPostReplaceStart(assignment_id=assignment.id),
        session,
        SETTINGS,
    )
    labels = button_labels(chat.last_markup())
    assert labels == ["@user3"], (
        "user1 is already on the review (being replaced or not), user2 is the other "
        "active reviewer, and alice is the author - none should be offered"
    )


async def test_replace_start_alerts_when_the_pool_has_no_one_left(session) -> None:
    review = await make_review(session)
    assignment = next(a for a in review.assignments if a.username == "user1")
    query = FakeQuery(FakeMessage(Chat()))
    await myposts.on_myposts_replace_start(
        query, MyPostReplaceStart(assignment_id=assignment.id), session, SETTINGS
    )
    assert query.alerts == [texts.t("ru", "myposts_no_candidates")]


# --- actually replacing --------------------------------------------------------------


async def test_replace_with_swaps_the_reviewer_and_keeps_the_other_untouched(
    session,
) -> None:
    review = await make_review(session)
    await admin_service.add_to_pool(
        session, username="user3", product=None, added_by_user_id=1
    )
    old = next(a for a in review.assignments if a.username == "user1")
    other = next(a for a in review.assignments if a.username == "user2")

    query = FakeQuery(FakeMessage(Chat()))
    await myposts.on_myposts_replace_with(
        query,
        MyPostReplaceWith(assignment_id=old.id, username="user3"),
        session,
        SETTINGS,
        FakeBot(),
    )

    active = {a.username for a in review.assignments if a.removed_at is None}
    assert active == {"user2", "user3"}
    assert old.removed_at is not None
    assert other.removed_at is None, "the untouched reviewer must stay untouched"
    assert query.alerts == [
        texts.t("ru", "myposts_replaced", old=old.display_label, new="user3")
    ]


async def test_replace_with_is_refused_to_someone_who_is_not_the_author(session) -> None:
    review = await make_review(session)
    old = next(a for a in review.assignments if a.username == "user1")
    query = FakeQuery(FakeMessage(Chat(), user=OTHER), user=OTHER)
    await myposts.on_myposts_replace_with(
        query,
        MyPostReplaceWith(assignment_id=old.id, username="user3"),
        session,
        SETTINGS,
        FakeBot(),
    )
    assert query.alerts == [texts.t("ru", "myposts_gone")]
    assert old.removed_at is None, "untouched"


async def test_replace_with_reviving_a_previously_removed_reviewer_does_not_crash(
    session,
) -> None:
    """A real edge case: the replacement was already named on this exact review at
    some point and later dropped by a post edit — `mention_key` collides, so this
    must revive that row rather than insert a duplicate and hit the unique
    constraint (`uq_assignment_per_review`)."""
    review = await make_review(session)
    old = next(a for a in review.assignments if a.username == "user1")
    user2 = next(a for a in review.assignments if a.username == "user2")
    user2.removed_at = msk(27, 11, 0)  # as if dropped by an earlier post edit
    await session.flush()

    query = FakeQuery(FakeMessage(Chat()))
    await myposts.on_myposts_replace_with(
        query,
        MyPostReplaceWith(assignment_id=old.id, username="user2"),
        session,
        SETTINGS,
        FakeBot(),
    )

    assert user2.removed_at is None, "revived, not duplicated"
    active_mention_keys = [a.mention_key for a in review.assignments if a.removed_at is None]
    assert active_mention_keys.count("un:user2") == 1
