"""The step-by-step /announce composer, driven through its states.

There is no aiogram dispatch harness in this repo, so the handlers are called
directly with stand-ins for Message/CallbackQuery over a real FSMContext — enough to
catch a broken transition, which is the whole risk in a multi-step flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from reviewpulse.config import Settings
from reviewpulse.db import repo
from reviewpulse.telegram import texts
from reviewpulse.telegram.handlers import announce
from reviewpulse.telegram.keyboards import AnnounceProduct, AnnounceStep

CHAT_ID = 555
USER_ID = 777

SETTINGS = Settings(
    _env_file=None,
    BOT_TOKEN="t",
    DEFAULT_LOCALE="ru",
    REVIEW_PROJECTS={
        "backend/api": {"product": "Demo Product", "techlead": "lead", "pool": ["pool1"]},
        "backend/other": {"product": "Other Product", "techlead": "lead2", "pool": ["pool2"]},
    },
)

USER = SimpleNamespace(
    id=USER_ID, username="author", full_name="Author", language_code="ru"
)


@dataclass
class FakeLink:
    offset: int
    length: int
    url: str
    type: str = "text_link"


@dataclass
class Chat:
    """Everything the bot said, plus the id counter that makes prompts distinguishable."""

    sent: list = field(default_factory=list)
    edits: list = field(default_factory=list)
    next_id: int = 100

    def last_prompt(self) -> FakeMessage:
        return self.sent[-1].message

    def last_text(self) -> str:
        return self.sent[-1].text


class FakeMessage:
    def __init__(self, chat: Chat, text=None, entities=None, message_id=1):
        self._chat = chat
        self.text = text
        self.entities = entities
        self.from_user = USER
        self.chat = SimpleNamespace(id=CHAT_ID, type="private")
        self.message_id = message_id

    async def answer(self, text, reply_markup=None, **kwargs):
        self._chat.next_id += 1
        sent = FakeMessage(self._chat, text=text, message_id=self._chat.next_id)
        self._chat.sent.append(
            SimpleNamespace(text=text, markup=reply_markup, message=sent)
        )
        return sent

    async def edit_text(self, text, **kwargs):
        self._chat.edits.append(text)

    async def edit_reply_markup(self, reply_markup=None):
        self._chat.edits.append(None)


class FakeQuery:
    def __init__(self, message: FakeMessage):
        self.message = message
        self.from_user = USER
        self.answers: list = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


def new_state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=USER_ID)
    )


async def skip(chat: Chat, state: FSMContext, session) -> FakeQuery:
    query = FakeQuery(chat.last_prompt())
    await announce.on_step_control(
        query, AnnounceStep(action="skip"), state, session, SETTINGS
    )
    return query


# --- the happy path, with merge requests ------------------------------------------


async def test_the_composer_walks_from_a_button_tap_to_a_ready_draft(session) -> None:
    chat, state = Chat(), new_state()

    await announce.on_announce_button(FakeMessage(chat), state, session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "announce_step_title")
    assert await state.get_state() == announce.Compose.title.state

    await announce.on_title(
        FakeMessage(chat, text="Доработка connection pool"), state, session, SETTINGS
    )
    assert chat.last_text() == texts.t("ru", "announce_step_merge_requests")

    await announce.on_merge_requests(
        FakeMessage(chat, text="https://git.example.com/backend/api/-/merge_requests/1112"),
        state,
        session,
        SETTINGS,
    )
    # With an MR the product is already known, so the product step is skipped.
    assert chat.last_text() == texts.t("ru", "announce_step_docs")

    docs_message = FakeMessage(
        chat,
        text="Confluence",
        entities=[FakeLink(0, 10, "https://wiki.example.com/pages/1")],
    )
    await announce.on_docs(docs_message, state, session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "announce_step_task")

    await announce.on_task(
        FakeMessage(chat, text="https://tasks.example.com/card/1"), state, session, SETTINGS
    )

    assert await state.get_state() is None, "the flow ends cleared"
    preview = chat.last_text()
    assert "Demo Product" in preview
    assert "Доработка connection pool" in preview
    assert "https://wiki.example.com/pages/1" in preview, "the hidden docs link survived"
    assert "@lead" in preview


async def test_a_docs_link_pasted_as_a_hyperlink_reaches_the_saved_draft(session) -> None:
    """The reported bug: "Документация: Confluence" published with no docs line at
    all, because the URL lives on the entity and never appears in the text."""
    chat, state = Chat(), new_state()
    await announce.on_announce_button(FakeMessage(chat), state, session, SETTINGS)
    await announce.on_title(FakeMessage(chat, text="Заголовок"), state, session, SETTINGS)
    await announce.on_merge_requests(
        FakeMessage(chat, text="https://git.example.com/backend/api/-/merge_requests/1"),
        state,
        session,
        SETTINGS,
    )
    await announce.on_docs(
        FakeMessage(
            chat, text="Confluence", entities=[FakeLink(0, 10, "https://wiki.example.com/x")]
        ),
        state,
        session,
        SETTINGS,
    )
    await skip(chat, state, session)

    draft = await repo.get_draft(session, 1)
    assert draft is not None
    assert draft.docs_url == "https://wiki.example.com/x"


# --- no merge request at all -------------------------------------------------------


async def test_skipping_the_mrs_asks_for_a_product_and_then_a_description(session) -> None:
    """An SQL-only change: no MR to infer the product from, and no docs page — the
    two branches the composer exists for."""
    chat, state = Chat(), new_state()

    await announce.on_announce_button(FakeMessage(chat), state, session, SETTINGS)
    await announce.on_title(
        FakeMessage(chat, text="Убрать некорректные данные"), state, session, SETTINGS
    )
    await skip(chat, state, session)

    assert chat.last_text() == texts.t("ru", "announce_step_product")
    assert await state.get_state() == announce.Compose.product.state
    assert [b.text for row in chat.sent[-1].markup.inline_keyboard for b in row][:2] == [
        "Demo Product",
        "Other Product",
    ]

    await announce.on_product_chosen(
        FakeQuery(chat.last_prompt()), AnnounceProduct(index=1), state, session, SETTINGS
    )
    assert chat.last_text() == texts.t("ru", "announce_step_docs")

    await skip(chat, state, session)
    assert chat.last_text() == texts.t("ru", "announce_step_description")

    await announce.on_description(
        FakeMessage(chat, text="Только SQL, кода нет"), state, session, SETTINGS
    )
    assert chat.last_text() == texts.t("ru", "announce_step_task")

    await skip(chat, state, session)

    draft = await repo.get_draft(session, 1)
    assert draft is not None
    assert draft.product == "Other Product"
    assert draft.description == "Только SQL, кода нет"
    assert repo.draft_merge_requests(draft) == []
    assert draft.techlead_username == "lead2", "reviewers follow the chosen product"


async def test_skipping_everything_refuses_to_build_an_empty_post(session) -> None:
    chat, state = Chat(), new_state()
    await announce.on_announce_button(FakeMessage(chat), state, session, SETTINGS)
    await announce.on_title(FakeMessage(chat, text="Заголовок"), state, session, SETTINGS)
    await skip(chat, state, session)  # no MRs
    await announce.on_product_chosen(
        FakeQuery(chat.last_prompt()), AnnounceProduct(index=0), state, session, SETTINGS
    )
    await skip(chat, state, session)  # no docs
    await skip(chat, state, session)  # no description
    await skip(chat, state, session)  # no task

    assert chat.last_text() == texts.t("ru", "announce_nothing_provided")
    assert await repo.get_draft(session, 1) is None


# --- guards -------------------------------------------------------------------------


async def test_a_skip_tapped_on_a_scrolled_up_prompt_is_refused(session) -> None:
    """Every prompt stays in the chat. Without this check, Skip on an older one would
    silently skip whatever step is current now."""
    chat, state = Chat(), new_state()
    await announce.on_announce_button(FakeMessage(chat), state, session, SETTINGS)
    await announce.on_title(FakeMessage(chat, text="Заголовок"), state, session, SETTINGS)
    stale = chat.last_prompt()

    await announce.on_merge_requests(
        FakeMessage(chat, text="https://git.example.com/backend/api/-/merge_requests/1"),
        state,
        session,
        SETTINGS,
    )
    assert await state.get_state() == announce.Compose.docs.state

    query = FakeQuery(stale)
    await announce.on_step_control(
        query, AnnounceStep(action="skip"), state, session, SETTINGS
    )

    assert query.answers == [texts.t("ru", "announce_draft_gone")]
    assert await state.get_state() == announce.Compose.docs.state, "still on the same step"


async def test_cancel_clears_the_flow(session) -> None:
    chat, state = Chat(), new_state()
    await announce.on_announce_button(FakeMessage(chat), state, session, SETTINGS)

    query = FakeQuery(chat.last_prompt())
    await announce.on_step_control(
        query, AnnounceStep(action="cancel"), state, session, SETTINGS
    )

    assert await state.get_state() is None
    assert chat.edits[-1] == texts.t("ru", "announce_cancelled")


async def test_an_unparseable_mr_message_reprompts_instead_of_advancing(session) -> None:
    chat, state = Chat(), new_state()
    await announce.on_announce_button(FakeMessage(chat), state, session, SETTINGS)
    await announce.on_title(FakeMessage(chat, text="Заголовок"), state, session, SETTINGS)

    await announce.on_merge_requests(
        FakeMessage(chat, text="потом пришлю"), state, session, SETTINGS
    )

    assert chat.last_text() == texts.t("ru", "announce_step_no_mr")
    assert await state.get_state() == announce.Compose.merge_requests.state


async def test_a_docs_message_with_no_link_reprompts(session) -> None:
    chat, state = Chat(), new_state()
    await announce.on_announce_button(FakeMessage(chat), state, session, SETTINGS)
    await announce.on_title(FakeMessage(chat, text="Заголовок"), state, session, SETTINGS)
    await announce.on_merge_requests(
        FakeMessage(chat, text="https://git.example.com/backend/api/-/merge_requests/1"),
        state,
        session,
        SETTINGS,
    )

    await announce.on_docs(FakeMessage(chat, text="нет пока"), state, session, SETTINGS)

    assert chat.last_text() == texts.t("ru", "announce_step_no_url")
    assert await state.get_state() == announce.Compose.docs.state


# --- the one-shot form ----------------------------------------------------------------


async def test_a_one_shot_announce_without_an_mr_hands_over_to_the_product_step(
    session,
) -> None:
    """Complaint from the team: "нельзя без МР-ов создать ревью". There is no repo to
    infer a product from, so it asks rather than refusing — keeping what was typed."""
    chat, state = Chat(), new_state()
    command = SimpleNamespace(args="Убрать данные\n\nЗадача: https://tasks.example.com/card/9")
    message = FakeMessage(chat, text=f"/announce {command.args}")

    await announce.on_announce(message, command, state, session, SETTINGS)

    assert chat.last_text() == texts.t("ru", "announce_step_product")
    data = await state.get_data()
    assert data["title"] == "Убрать данные"
    assert data["task_url"] == "https://tasks.example.com/card/9"


async def test_a_one_shot_announce_with_an_mr_goes_straight_to_the_preview(session) -> None:
    chat, state = Chat(), new_state()
    args = "Доработка\n\nhttps://git.example.com/backend/api/-/merge_requests/5"
    command = SimpleNamespace(args=args)

    await announce.on_announce(
        FakeMessage(chat, text=f"/announce {args}"), command, state, session, SETTINGS
    )

    assert await state.get_state() is None
    assert "Demo Product" in chat.last_text()
    draft = await repo.get_draft(session, 1)
    assert draft is not None
    assert [ref.iid for ref in repo.draft_merge_requests(draft)] == [5]
