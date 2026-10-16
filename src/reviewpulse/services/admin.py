"""Bot admins and the shared reviewer pool — both DB-backed and editable through the
bot itself instead of a `REVIEW_PROJECTS`/`.env` redeploy.

Kept as one module since they share the same access rule: editing either requires
being an admin, and admin-ness itself is defined here.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import repo


class InvalidUsername(Exception):
    """Whatever was sent doesn't look like a Telegram @handle."""


def normalize_username(raw: str) -> str:
    """Strip a leading "@" and surrounding space; raise if what's left isn't a
    plausible Telegram username (5-32 chars, letters/digits/underscore, see
    https://core.telegram.org/method/account.checkUsername)."""
    username = raw.strip().lstrip("@")
    if not (5 <= len(username) <= 32) or not all(c.isalnum() or c == "_" for c in username):
        raise InvalidUsername(raw)
    return username


async def is_admin(session: AsyncSession, settings: Settings, telegram_user_id: int) -> bool:
    """DB-backed, with `Settings.admin_user_ids` as the fallback for an empty table —
    bootstrap on first deploy, and the safety net if every admin ever removes
    themselves (see `BotAdmin`'s docstring). Never both at once: the env list stops
    mattering the moment the table has at least one row — which is exactly why
    `ensure_admin_seeded` exists: without it, an env-listed admin acting while the
    table is still empty (e.g. adding the *next* admin) would strand themselves the
    instant that first row lands.
    """
    admins = await repo.list_admins(session)
    if admins:
        return any(admin.telegram_user_id == telegram_user_id for admin in admins)
    return telegram_user_id in settings.admin_user_ids


async def ensure_admin_seeded(
    session: AsyncSession, settings: Settings, *, telegram_user_id: int, username: str | None
) -> None:
    """Call this once an env-fallback admin is actually about to use their access
    (opening the management panel, say) — turns the implicit, config-only grant into
    a real `BotAdmin` row before anything else can be added, so adding someone else
    next can never accidentally remove *this* admin's own access.

    A no-op once the table is non-empty, by design: at that point `is_admin` no
    longer consults the env list at all, so there is nothing left to seed from it.
    """
    admins = await repo.list_admins(session)
    if not admins and telegram_user_id in settings.admin_user_ids:
        await repo.add_admin(
            session,
            telegram_user_id=telegram_user_id,
            username=username,
            added_by_user_id=telegram_user_id,
        )


async def list_admins_for_display(
    session: AsyncSession, settings: Settings
) -> list[tuple[int, str | None]]:
    """`(telegram_user_id, username_or_None)` pairs for the public admin list —
    whichever set `is_admin` would actually consult right now."""
    admins = await repo.list_admins(session)
    if admins:
        return [(admin.telegram_user_id, admin.username) for admin in admins]

    pairs: list[tuple[int, str | None]] = []
    for telegram_user_id in settings.admin_user_ids:
        user = await repo.get_user_by_telegram_id(session, telegram_user_id)
        pairs.append((telegram_user_id, user.username if user else None))
    return pairs


async def add_admin(
    session: AsyncSession, *, telegram_user_id: int, username: str | None, added_by_user_id: int
) -> bool:
    """`False` if already an admin."""
    admin = await repo.add_admin(
        session,
        telegram_user_id=telegram_user_id,
        username=username,
        added_by_user_id=added_by_user_id,
    )
    return admin is not None


async def remove_admin(session: AsyncSession, telegram_user_id: int) -> bool:
    return await repo.remove_admin(session, telegram_user_id)


async def pool_usernames(session: AsyncSession, product: str) -> list[str]:
    """The effective draw list for `product` — its own members plus every global
    one. Deduplicated: a username with both a global and a product-specific row
    (redundant, but allowed — see `ReviewerPoolMember`) must not be drawn twice."""
    seen: list[str] = []
    for member in await repo.pool_members_for_product(session, product):
        if member.username not in seen:
            seen.append(member.username)
    return seen


async def pool_members_for_display(
    session: AsyncSession, product: str | None
) -> list[tuple[str, bool]]:
    """`(username, is_global)` pairs for the `/manage` remove list — `product=None`
    means the "🌐 all products" scope (global members only); a specific product
    shows everyone effectively in its pool, global members flagged so removing one
    there is understood to remove them everywhere, not just from this product."""
    if product is None:
        members = await repo.global_pool_members(session)
    else:
        members = await repo.pool_members_for_product(session, product)
    return [(member.username, member.product is None) for member in members]


async def add_to_pool(
    session: AsyncSession, *, username: str, product: str | None, added_by_user_id: int
) -> bool:
    """`False` if already (effectively) in `product`'s pool. `product=None` adds
    them for every product at once."""
    member = await repo.add_pool_member(
        session, username=username, product=product, added_by_user_id=added_by_user_id
    )
    return member is not None


async def remove_from_pool(session: AsyncSession, *, username: str, product: str | None) -> bool:
    """Removes the exact `(product, username)` row — see `repo.remove_pool_member`."""
    return await repo.remove_pool_member(session, product=product, username=username)
