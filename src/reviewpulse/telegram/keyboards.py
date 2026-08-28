"""Inline keyboards and their callback payloads.

Callback data is the whole reason this design works: unlike a channel reaction, a
button press always tells us *who* pressed it.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from ..i18n import SUPPORTED_LOCALES
from . import texts


class ReviewAction(CallbackData, prefix="rv"):
    review_id: int
    action: str  # approve | changes | fixed | close | claim


class SnoozeAction(CallbackData, prefix="sn"):
    assignment_id: int
    hours: int  # 0 means "until tomorrow morning"


class AnnounceAction(CallbackData, prefix="an"):
    draft_id: int
    action: str  # publish | reroll | cancel


class AnnounceStep(CallbackData, prefix="aw"):
    """A control on one step of the step-by-step composer, not on a saved draft —
    the draft doesn't exist yet, so there is no id to carry."""

    action: str  # skip | cancel


class AnnounceProduct(CallbackData, prefix="ap"):
    #: Index into `services.announcements.available_products`, not the product name:
    #: callback_data is capped at 64 bytes and these names are non-ASCII.
    index: int


def review_card(
    review_id: int, locale: str, *, is_closed: bool, needs_reviewers: bool
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if is_closed:
        return builder.as_markup()

    if needs_reviewers:
        builder.button(
            text=texts.t(locale, "btn_claim"),
            callback_data=ReviewAction(review_id=review_id, action="claim"),
        )
        return builder.as_markup()

    builder.button(
        text=texts.t(locale, "btn_approve"),
        callback_data=ReviewAction(review_id=review_id, action="approve"),
    )
    builder.button(
        text=texts.t(locale, "btn_request_changes"),
        callback_data=ReviewAction(review_id=review_id, action="changes"),
    )
    builder.button(
        text=texts.t(locale, "btn_fixed"),
        callback_data=ReviewAction(review_id=review_id, action="fixed"),
    )
    builder.button(
        text=texts.t(locale, "btn_close"),
        callback_data=ReviewAction(review_id=review_id, action="close"),
    )
    builder.adjust(2, 2)
    return builder.as_markup()


def announce_preview(
    draft_id: int, locale: str, *, has_pool_slot: bool
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.t(locale, "btn_announce_publish"),
        callback_data=AnnounceAction(draft_id=draft_id, action="publish"),
    )
    if has_pool_slot:
        builder.button(
            text=texts.t(locale, "btn_announce_reroll"),
            callback_data=AnnounceAction(draft_id=draft_id, action="reroll"),
        )
    builder.button(
        text=texts.t(locale, "btn_announce_cancel"),
        callback_data=AnnounceAction(draft_id=draft_id, action="cancel"),
    )
    builder.adjust(2, 1) if has_pool_slot else builder.adjust(2)
    return builder.as_markup()


# A reply-keyboard button carries no callback_data — Telegram just sends its label
# back as plain text — so a tap has to be recognized by text alone. The composer's
# own locale isn't known until the DB is queried inside the handler, so each set below
# is every locale's rendering of one button, and the handler matches against the
# whole set rather than a single hardcoded string.
MENU_STATUS_TEXTS = frozenset(texts.t(loc, "btn_menu_status") for loc in SUPPORTED_LOCALES)
MENU_ANNOUNCE_TEXTS = frozenset(texts.t(loc, "btn_menu_announce") for loc in SUPPORTED_LOCALES)
MENU_STATS_TEXTS = frozenset(texts.t(loc, "btn_menu_stats") for loc in SUPPORTED_LOCALES)


def main_menu(locale: str, *, show_stats: bool) -> ReplyKeyboardMarkup:
    """The persistent bottom keyboard - Status/Announce always, Stats only for the
    configured recipients (mirrors the access check `on_stats` makes anyway)."""
    builder = ReplyKeyboardBuilder()
    builder.button(text=texts.t(locale, "btn_menu_status"))
    builder.button(text=texts.t(locale, "btn_menu_announce"))
    if show_stats:
        builder.button(text=texts.t(locale, "btn_menu_stats"))
        builder.adjust(2, 1)
    else:
        builder.adjust(2)
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def announce_step(locale: str, *, can_skip: bool) -> InlineKeyboardMarkup:
    """Controls under one composer prompt. Cancel is always available; Skip only on
    the steps that are genuinely optional (the title never is)."""
    builder = InlineKeyboardBuilder()
    if can_skip:
        builder.button(
            text=texts.t(locale, "btn_announce_skip"),
            callback_data=AnnounceStep(action="skip"),
        )
    builder.button(
        text=texts.t(locale, "btn_announce_cancel"),
        callback_data=AnnounceStep(action="cancel"),
    )
    builder.adjust(2 if can_skip else 1)
    return builder.as_markup()


def announce_products(products: list[str], locale: str) -> InlineKeyboardMarkup:
    """One button per configured product, plus Cancel — the MR-less branch, where
    there is no repo to infer the product from."""
    builder = InlineKeyboardBuilder()
    for index, product in enumerate(products):
        builder.button(text=product, callback_data=AnnounceProduct(index=index))
    builder.button(
        text=texts.t(locale, "btn_announce_cancel"),
        callback_data=AnnounceStep(action="cancel"),
    )
    builder.adjust(*([2] * ((len(products) + 1) // 2)), 1)
    return builder.as_markup()


def nudge_actions(locale: str, assignment_id: int, review_url: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if review_url:
        builder.row(InlineKeyboardButton(text=texts.t(locale, "btn_open_review"), url=review_url))
    builder.row(
        InlineKeyboardButton(
            text=texts.t(locale, "btn_snooze_hour"),
            callback_data=SnoozeAction(assignment_id=assignment_id, hours=1).pack(),
        ),
        InlineKeyboardButton(
            text=texts.t(locale, "btn_snooze_tomorrow"),
            callback_data=SnoozeAction(assignment_id=assignment_id, hours=0).pack(),
        ),
    )
    return builder.as_markup()
