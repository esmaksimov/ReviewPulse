"""The admin-management handlers, driven directly the same way
`test_announce_flow.py` drives the composer — no aiogram dispatch harness here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from reviewpulse.config import Settings
from reviewpulse.db import repo
from reviewpulse.services import admin as admin_service
from reviewpulse.telegram import texts
from reviewpulse.telegram.handlers import admin, build_router
from reviewpulse.telegram.keyboards import ManageAction, RemoveAdmin, RemovePoolMember

CHAT_ID = 555
ADMIN_ID = 1
OTHER_ID = 2

SETTINGS = Settings(_env_file=None, BOT_TOKEN="t", DEFAULT_LOCALE="ru", ADMIN_USER_IDS=[ADMIN_ID])

ADMIN_USER = SimpleNamespace(id=ADMIN_ID, username="admin", full_name="Admin", language_code="ru")
OTHER_USER = SimpleNamespace(id=OTHER_ID, username="other", full_name="Other", language_code="ru")


@dataclass
class Chat:
    sent: list = field(default_factory=list)

    def last_text(self) -> str:
        return self.sent[-1].text

    def last_markup(self):
        return self.sent[-1].markup


class FakeMessage:
    def __init__(self, chat: Chat, user=ADMIN_USER, text=None, message_id=1):
        self._chat = chat
        self.text = text
        self.from_user = user
        self.chat = SimpleNamespace(id=CHAT_ID, type="private")
        self.message_id = message_id

    async def answer(self, text, reply_markup=None, **kwargs):
        self._chat.sent.append(SimpleNamespace(text=text, markup=reply_markup))
        return FakeMessage(self._chat, user=self.from_user)


class FakeQuery:
    def __init__(self, message: FakeMessage, user=ADMIN_USER):
        self.message = message
        self.from_user = user
        self.answers: list = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


def new_state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=1))


# --- /admins: public ------------------------------------------------------------


async def test_admins_view_lists_the_env_fallback_when_the_table_is_empty(session) -> None:
    await repo.upsert_user(session, ADMIN_ID, username="admin")
    chat = Chat()
    await admin.on_admins(FakeMessage(chat, user=OTHER_USER), session, SETTINGS)
    assert "@admin" in chat.last_text()


async def test_admins_view_is_available_to_a_non_admin(session) -> None:
    """Visibility is unconditional — only *editing* is gated (see `on_manage`)."""
    chat = Chat()
    await admin.on_admins(FakeMessage(chat, user=OTHER_USER), session, SETTINGS)
    assert texts.t("ru", "admins_header") in chat.last_text()


# --- /manage: admin-only --------------------------------------------------------


async def test_manage_refuses_a_non_admin(session) -> None:
    chat, state = Chat(), new_state()
    await admin.on_manage(FakeMessage(chat, user=OTHER_USER), state, session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "manage_no_access")


async def test_manage_opens_the_panel_for_an_admin(session) -> None:
    chat, state = Chat(), new_state()
    await admin.on_manage(FakeMessage(chat, user=ADMIN_USER), state, session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "manage_header")
    assert chat.last_markup() is not None


async def test_manage_action_is_re_checked_even_if_a_non_admin_reaches_it(session) -> None:
    """Same caution as `commands.on_stats` — someone could type the button's label
    (or replay an old callback) by hand without ever passing the entry gate."""
    chat, state = Chat(), new_state()
    query = FakeQuery(FakeMessage(chat, user=OTHER_USER), user=OTHER_USER)
    await admin.on_manage_action(
        query, ManageAction(action="pool_add"), state, session, SETTINGS
    )
    assert query.answers == [texts.t("ru", "manage_no_access")]


# --- adding to the pool ----------------------------------------------------------


async def test_pool_add_flow_adds_a_new_username(session) -> None:
    chat, state = Chat(), new_state()
    query = FakeQuery(FakeMessage(chat))
    await admin.on_manage_action(query, ManageAction(action="pool_add"), state, session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "manage_ask_pool_username")

    await admin.on_pool_username(FakeMessage(chat, text="@newbie"), state, session, SETTINGS)

    assert chat.last_text() == texts.t("ru", "manage_pool_added", username="newbie")
    assert await admin_service.pool_usernames(session) == ["newbie"]
    assert await state.get_state() is None


async def test_pool_add_reports_when_already_present(session) -> None:
    await admin_service.add_to_pool(session, username="newbie", added_by_user_id=ADMIN_ID)
    chat, state = Chat(), new_state()
    await admin.on_manage_action(
        FakeQuery(FakeMessage(chat)), ManageAction(action="pool_add"), state, session, SETTINGS
    )
    await admin.on_pool_username(FakeMessage(chat, text="newbie"), state, session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "manage_pool_already", username="newbie")


async def test_pool_add_reprompts_on_an_invalid_username_without_losing_the_state(
    session,
) -> None:
    chat, state = Chat(), new_state()
    await admin.on_manage_action(
        FakeQuery(FakeMessage(chat)), ManageAction(action="pool_add"), state, session, SETTINGS
    )
    await admin.on_pool_username(FakeMessage(chat, text="no"), state, session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "manage_invalid_username")
    assert (await state.get_state()) == admin.ManageFlow.pool_username.state


async def test_pool_remove_offers_nothing_when_the_pool_is_empty(session) -> None:
    chat, state = Chat(), new_state()
    query = FakeQuery(FakeMessage(chat))
    await admin.on_manage_action(
        query, ManageAction(action="pool_remove"), state, session, SETTINGS
    )
    assert query.answers == [texts.t("ru", "manage_pool_empty")]


async def test_pool_remove_button_removes_the_tapped_member(session) -> None:
    await admin_service.add_to_pool(session, username="alice", added_by_user_id=ADMIN_ID)
    chat = Chat()
    await admin.on_remove_pool_member(
        FakeQuery(FakeMessage(chat)), RemovePoolMember(username="alice"), session, SETTINGS
    )
    assert await admin_service.pool_usernames(session) == []


async def test_pool_remove_button_is_refused_for_a_non_admin(session) -> None:
    await admin_service.add_to_pool(session, username="alice", added_by_user_id=ADMIN_ID)
    query = FakeQuery(FakeMessage(Chat(), user=OTHER_USER), user=OTHER_USER)
    await admin.on_remove_pool_member(
        query, RemovePoolMember(username="alice"), session, SETTINGS
    )
    assert await admin_service.pool_usernames(session) == ["alice"], "untouched"


# --- adding/removing admins ------------------------------------------------------


async def test_admin_add_requires_the_target_to_be_a_known_user(session) -> None:
    chat, state = Chat(), new_state()
    await admin.on_manage_action(
        FakeQuery(FakeMessage(chat)), ManageAction(action="admin_add"), state, session, SETTINGS
    )
    await admin.on_admin_username(FakeMessage(chat, text="@ghost"), state, session, SETTINGS)
    assert chat.last_text() == texts.t("ru", "manage_admin_unknown_user", username="ghost")
    # Stays in the state — unlike an invalid-shape username, this one might become
    # valid once the person /starts the bot, so it isn't worth losing the flow over.
    assert (await state.get_state()) == admin.ManageFlow.admin_username.state


async def test_admin_add_succeeds_for_a_known_user(session) -> None:
    await repo.upsert_user(session, 999, username="newadmin")
    chat, state = Chat(), new_state()
    await admin.on_manage_action(
        FakeQuery(FakeMessage(chat)), ManageAction(action="admin_add"), state, session, SETTINGS
    )
    await admin.on_admin_username(FakeMessage(chat, text="@newadmin"), state, session, SETTINGS)

    assert chat.last_text() == texts.t("ru", "manage_admin_added", username="newadmin")


async def test_the_bootstrap_admin_is_not_stranded_by_adding_someone_else(session) -> None:
    """The actual bug this guards against: before `ensure_admin_seeded`, the
    env-only bootstrap admin lost access the instant the table they'd never been
    written to stopped being empty — i.e. the moment they added anyone else."""
    await repo.upsert_user(session, 999, username="newadmin")
    chat, state = Chat(), new_state()

    await admin.on_manage(FakeMessage(chat, user=ADMIN_USER), state, session, SETTINGS)
    await admin.on_manage_action(
        FakeQuery(FakeMessage(chat), user=ADMIN_USER),
        ManageAction(action="admin_add"),
        state,
        session,
        SETTINGS,
    )
    await admin.on_admin_username(
        FakeMessage(chat, user=ADMIN_USER, text="@newadmin"), state, session, SETTINGS
    )

    assert await admin_service.is_admin(session, SETTINGS, ADMIN_ID) is True
    assert await admin_service.is_admin(session, SETTINGS, 999) is True
    assert await admin_service.is_admin(session, SETTINGS, 999) is True


async def test_admin_remove_offers_nothing_when_the_db_table_is_empty(session) -> None:
    """The env fallback list isn't removable through the bot at all (see
    `manage_admins_empty`) — only real `BotAdmin` rows are."""
    chat, state = Chat(), new_state()
    query = FakeQuery(FakeMessage(chat))
    await admin.on_manage_action(
        query, ManageAction(action="admin_remove"), state, session, SETTINGS
    )
    assert query.answers == [texts.t("ru", "manage_admins_empty")]


async def test_admin_remove_button_removes_the_tapped_admin(session) -> None:
    # Realistic prior state: the acting admin is themselves a DB row too — exactly
    # what `ensure_admin_seeded` guarantees once the table stops being empty, so
    # removing someone else here can never strand the admin doing the removing.
    await admin_service.add_admin(
        session, telegram_user_id=ADMIN_ID, username="admin", added_by_user_id=ADMIN_ID
    )
    await admin_service.add_admin(
        session, telegram_user_id=999, username="bob", added_by_user_id=ADMIN_ID
    )
    await admin.on_remove_admin(
        FakeQuery(FakeMessage(Chat())), RemoveAdmin(telegram_user_id=999), session, SETTINGS
    )
    assert await admin_service.is_admin(session, SETTINGS, 999) is False
    assert await admin_service.is_admin(session, SETTINGS, ADMIN_ID) is True, (
        "removing someone else must never strand the acting admin"
    )


# --- a menu button mid-flow must switch screens, not be read as input -----------


async def test_tapping_the_announce_button_while_mid_pool_add_switches_to_announce(
    session,
) -> None:
    """Regression for a real production report: a tap on a *different* menu button
    while `/manage` was waiting for a pool username got read as an (invalid)
    username instead of switching screens. `on_pool_username`/`on_admin_username`
    only excluded slash commands, not other menu buttons, and `admin.router` is
    checked ahead of `announce.router` in the real dispatch chain — so the state
    handler swallowed the tap before `/announce`'s own entry point ever saw it.

    This drives the actual router tree (`build_router`), not the handler function
    directly — the bug is about dispatch/filter precedence between two different
    routers, which calling a handler by hand can never exercise."""
    router = build_router()
    chat, state = Chat(), new_state()
    await admin.on_manage_action(
        FakeQuery(FakeMessage(chat)), ManageAction(action="pool_add"), state, session, SETTINGS
    )
    assert chat.last_text() == texts.t("ru", "manage_ask_pool_username")

    announce_button = FakeMessage(chat, text=texts.t("ru", "btn_menu_announce"))
    await router.propagate_event(
        "message",
        announce_button,
        state=state,
        session=session,
        settings=SETTINGS,
        bot=object(),  # only `Command`/`CommandStart` filters need it, and they
        # reject our non-`Message` fakes before ever touching it
        raw_state=await state.get_state(),
    )

    assert chat.last_text() == texts.t("ru", "announce_step_title")
    assert await state.get_state() == "Compose:title"
