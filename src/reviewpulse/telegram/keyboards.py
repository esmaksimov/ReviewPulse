"""Inline keyboards and their callback payloads.

Callback data is the whole reason this design works: unlike a channel reaction, a
button press always tells us *who* pressed it.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import texts


class ReviewAction(CallbackData, prefix="rv"):
    review_id: int
    action: str  # approve | changes | fixed | close | claim


class SnoozeAction(CallbackData, prefix="sn"):
    assignment_id: int
    hours: int  # 0 means "until tomorrow morning"


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
