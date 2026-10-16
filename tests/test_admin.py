"""services.admin: bot admins and the shared reviewer pool, both DB-backed."""

from __future__ import annotations

import pytest

from reviewpulse.config import Settings
from reviewpulse.services import admin as admin_service


def make_settings(**kwargs) -> Settings:
    return Settings(_env_file=None, BOT_TOKEN="t", **kwargs)


# --- normalize_username (pure) ------------------------------------------------


def test_normalize_username_strips_a_leading_at() -> None:
    assert admin_service.normalize_username("@alice") == "alice"


def test_normalize_username_strips_surrounding_space() -> None:
    assert admin_service.normalize_username("  alice  ") == "alice"


def test_normalize_username_rejects_something_too_short() -> None:
    with pytest.raises(admin_service.InvalidUsername):
        admin_service.normalize_username("bob")


def test_normalize_username_rejects_punctuation() -> None:
    with pytest.raises(admin_service.InvalidUsername):
        admin_service.normalize_username("not a username!")


# --- is_admin: DB-first, env as the empty-table fallback -----------------------


async def test_is_admin_uses_the_env_list_when_the_table_is_empty(session) -> None:
    settings = make_settings(ADMIN_USER_IDS="111,222")
    assert await admin_service.is_admin(session, settings, 111) is True
    assert await admin_service.is_admin(session, settings, 999) is False


async def test_is_admin_prefers_the_db_once_it_has_any_row(session) -> None:
    """The env list stops mattering the moment the table has at least one admin —
    not layered on top of it, a full handoff (see `BotAdmin`'s docstring)."""
    settings = make_settings(ADMIN_USER_IDS="111")
    await admin_service.add_admin(
        session, telegram_user_id=222, username="bob", added_by_user_id=111
    )
    assert await admin_service.is_admin(session, settings, 222) is True
    assert await admin_service.is_admin(session, settings, 111) is False, (
        "111 is only in the env fallback, which no longer applies"
    )


async def test_add_admin_is_idempotent(session) -> None:
    first = await admin_service.add_admin(
        session, telegram_user_id=1, username="alice", added_by_user_id=1
    )
    second = await admin_service.add_admin(
        session, telegram_user_id=1, username="alice", added_by_user_id=1
    )
    assert first is True
    assert second is False


async def test_remove_admin_reports_whether_anything_was_removed(session) -> None:
    await admin_service.add_admin(
        session, telegram_user_id=1, username="alice", added_by_user_id=1
    )
    assert await admin_service.remove_admin(session, 1) is True
    assert await admin_service.remove_admin(session, 1) is False


async def test_list_admins_for_display_falls_back_to_env_ids_with_known_usernames(
    session,
) -> None:
    from reviewpulse.db import repo

    await repo.upsert_user(session, 111, username="alice")
    settings = make_settings(ADMIN_USER_IDS="111,222")

    pairs = await admin_service.list_admins_for_display(session, settings)

    assert (111, "alice") in pairs
    assert (222, None) in pairs, "222 was never seen by the bot, so no username is known"


async def test_list_admins_for_display_uses_the_db_once_populated(session) -> None:
    settings = make_settings(ADMIN_USER_IDS="111")
    await admin_service.add_admin(
        session, telegram_user_id=222, username="bob", added_by_user_id=111
    )
    assert await admin_service.list_admins_for_display(session, settings) == [(222, "bob")]


# --- the per-product reviewer pool -----------------------------------------------


async def test_add_to_pool_is_idempotent_case_insensitively(session) -> None:
    first = await admin_service.add_to_pool(
        session, username="Alice", product="Demo", added_by_user_id=1
    )
    second = await admin_service.add_to_pool(
        session, username="alice", product="Demo", added_by_user_id=1
    )
    assert first is True
    assert second is False
    assert await admin_service.pool_usernames(session, "Demo") == ["Alice"]


async def test_remove_from_pool_reports_whether_anything_was_removed(session) -> None:
    await admin_service.add_to_pool(
        session, username="alice", product="Demo", added_by_user_id=1
    )
    assert await admin_service.remove_from_pool(session, username="alice", product="Demo") is True
    assert (
        await admin_service.remove_from_pool(session, username="alice", product="Demo") is False
    )
    assert await admin_service.pool_usernames(session, "Demo") == []


async def test_a_global_member_is_in_every_products_effective_pool(session) -> None:
    await admin_service.add_to_pool(session, username="alice", product=None, added_by_user_id=1)
    assert await admin_service.pool_usernames(session, "Demo") == ["alice"]
    assert await admin_service.pool_usernames(session, "Other") == ["alice"]


async def test_a_product_specific_member_does_not_leak_into_other_products(session) -> None:
    await admin_service.add_to_pool(session, username="bob", product="Demo", added_by_user_id=1)
    assert await admin_service.pool_usernames(session, "Demo") == ["bob"]
    assert await admin_service.pool_usernames(session, "Other") == []


async def test_adding_someone_already_global_to_a_specific_product_is_a_no_op(session) -> None:
    """They already cover that product via the global row — a redundant
    product-specific row on top would just need deduplicating later for nothing."""
    await admin_service.add_to_pool(session, username="alice", product=None, added_by_user_id=1)
    added = await admin_service.add_to_pool(
        session, username="alice", product="Demo", added_by_user_id=1
    )
    assert added is False


async def test_removing_from_one_product_does_not_touch_a_global_membership(session) -> None:
    await admin_service.add_to_pool(session, username="alice", product=None, added_by_user_id=1)
    removed = await admin_service.remove_from_pool(session, username="alice", product="Demo")
    assert removed is False, "there was never a Demo-specific row to remove"
    assert await admin_service.pool_usernames(session, "Demo") == ["alice"], (
        "the global row is untouched"
    )
