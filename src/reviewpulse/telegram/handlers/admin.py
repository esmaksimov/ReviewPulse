"""Bot admins and the shared reviewer pool, managed from inside Telegram.

Two access levels, not one: `/admins` just lists who currently has extra control —
visible to everyone, the same way `/products` shows the reviewer setup without
letting anyone change it. `/manage` is where the actual edits happen, gated to
admins only (re-checked on every action, not just on entry, the same "someone could
still type the button's label by hand" caution `commands.on_stats` already applies).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...db import repo
from ...i18n import resolve_locale
from ...services import admin as admin_service
from .. import keyboards, texts
from ..keyboards import (
    MENU_ADMINS_TEXTS,
    MENU_MANAGE_TEXTS,
    ManageAction,
    RemoveAdmin,
    RemovePoolMember,
)

router = Router(name="admin")
router.message.filter(F.chat.type == "private")


class ManageFlow(StatesGroup):
    pool_username = State()
    admin_username = State()


async def _locale_for(session: AsyncSession, user: User, settings: Settings) -> str:
    row = await repo.upsert_user(
        session, user.id, user.username, user.full_name, user.language_code
    )
    return resolve_locale(row.locale, user.language_code, default=settings.default_locale)


# --- public: who has extra control --------------------------------------------


@router.message(Command("admins"))
@router.message(F.text.in_(MENU_ADMINS_TEXTS))
async def on_admins(message: Message, session: AsyncSession, settings: Settings) -> None:
    await _show_admins(message, session, settings)


async def _show_admins(message: Message, session: AsyncSession, settings: Settings) -> None:
    locale = await _locale_for(session, message.from_user, settings)
    admins = await admin_service.list_admins_for_display(session, settings)
    lines = [texts.t(locale, "admins_header"), ""]
    if not admins:
        lines.append(texts.t(locale, "admins_empty"))
    else:
        lines.extend(
            f"• @{username}" if username else f"• {telegram_user_id}"
            for telegram_user_id, username in admins
        )
    await message.answer("\n".join(lines))


# --- admin-only: the management panel ------------------------------------------


@router.message(Command("manage"))
@router.message(F.text.in_(MENU_MANAGE_TEXTS))
async def on_manage(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    await _open_panel(message, state, session, settings)


async def _open_panel(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    """Registered ahead of the username-capture handlers below on purpose — tapping
    the menu button mid-flow restarts cleanly rather than being swallowed as an
    answer (same reasoning as `handlers.announce.on_announce_button`)."""
    locale = await _locale_for(session, message.from_user, settings)
    if not await admin_service.is_admin(session, settings, message.from_user.id):
        await message.answer(texts.t(locale, "manage_no_access"))
        return
    await state.clear()
    await message.answer(
        texts.t(locale, "manage_header"), reply_markup=keyboards.manage_panel(locale)
    )


@router.callback_query(ManageAction.filter())
async def on_manage_action(
    query: CallbackQuery,
    callback_data: ManageAction,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    locale = await _locale_for(session, query.from_user, settings)
    if not await admin_service.is_admin(session, settings, query.from_user.id):
        await query.answer(texts.t(locale, "manage_no_access"), show_alert=True)
        return

    if callback_data.action == "pool_add":
        await state.set_state(ManageFlow.pool_username)
        await query.message.answer(texts.t(locale, "manage_ask_pool_username"))
    elif callback_data.action == "pool_remove":
        pool = await admin_service.pool_usernames(session)
        if not pool:
            await query.answer(texts.t(locale, "manage_pool_empty"), show_alert=True)
            return
        await query.message.answer(
            texts.t(locale, "btn_manage_pool_remove"),
            reply_markup=keyboards.pool_remove_list(pool),
        )
    elif callback_data.action == "admin_add":
        await state.set_state(ManageFlow.admin_username)
        await query.message.answer(texts.t(locale, "manage_ask_admin_username"))
    elif callback_data.action == "admin_remove":
        admins = await repo.list_admins(session)
        if not admins:
            await query.answer(texts.t(locale, "manage_admins_empty"), show_alert=True)
            return
        pairs = [(row.telegram_user_id, row.username) for row in admins]
        await query.message.answer(
            texts.t(locale, "btn_manage_admin_remove"),
            reply_markup=keyboards.admin_remove_list(pairs),
        )
    await query.answer()


@router.message(ManageFlow.pool_username, F.text, ~F.text.startswith("/"))
async def on_pool_username(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, message.from_user, settings)
    try:
        username = admin_service.normalize_username(message.text)
    except admin_service.InvalidUsername:
        await message.answer(texts.t(locale, "manage_invalid_username"))
        return

    await state.clear()
    added = await admin_service.add_to_pool(
        session, username=username, added_by_user_id=message.from_user.id
    )
    key = "manage_pool_added" if added else "manage_pool_already"
    await message.answer(texts.t(locale, key, username=username))


@router.message(ManageFlow.admin_username, F.text, ~F.text.startswith("/"))
async def on_admin_username(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, message.from_user, settings)
    try:
        username = admin_service.normalize_username(message.text)
    except admin_service.InvalidUsername:
        await message.answer(texts.t(locale, "manage_invalid_username"))
        return

    target = await repo.get_user_by_username(session, username)
    if target is None:
        # Unlike the reviewer pool (plain usernames, resolved lazily — see
        # `services.reviews._sync_assignments`), `BotAdmin.telegram_user_id` is
        # required: `is_admin` checks the *pressing* user's numeric id, so there is
        # no admin row to create at all until this person is a known `User`.
        await message.answer(texts.t(locale, "manage_admin_unknown_user", username=username))
        return

    await state.clear()
    # If the acting admin is only in the table by way of the env fallback, seed them
    # a real row *before* adding `target` — otherwise this exact action would strand
    # them the instant the table stops being empty (see `ensure_admin_seeded`).
    await admin_service.ensure_admin_seeded(
        session,
        settings,
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
    )
    added = await admin_service.add_admin(
        session,
        telegram_user_id=target.telegram_user_id,
        username=target.username,
        added_by_user_id=message.from_user.id,
    )
    key = "manage_admin_added" if added else "manage_admin_already"
    await message.answer(texts.t(locale, key, username=username))


@router.callback_query(RemovePoolMember.filter())
async def on_remove_pool_member(
    query: CallbackQuery, callback_data: RemovePoolMember, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, query.from_user, settings)
    if not await admin_service.is_admin(session, settings, query.from_user.id):
        await query.answer(texts.t(locale, "manage_no_access"), show_alert=True)
        return
    await admin_service.remove_from_pool(session, callback_data.username)
    await query.answer(
        texts.t(locale, "manage_pool_removed", username=callback_data.username)
    )


@router.callback_query(RemoveAdmin.filter())
async def on_remove_admin(
    query: CallbackQuery, callback_data: RemoveAdmin, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, query.from_user, settings)
    if not await admin_service.is_admin(session, settings, query.from_user.id):
        await query.answer(texts.t(locale, "manage_no_access"), show_alert=True)
        return
    await admin_service.remove_admin(session, callback_data.telegram_user_id)
    await query.answer(texts.t(locale, "manage_admin_removed"))
