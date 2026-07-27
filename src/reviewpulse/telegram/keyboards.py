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


def review_card(review_id: int, *, is_closed: bool, needs_reviewers: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if is_closed:
        return builder.as_markup()

    if needs_reviewers:
        builder.button(
            text="🙋 Я ревьювер",
            callback_data=ReviewAction(review_id=review_id, action="claim"),
        )
        return builder.as_markup()

    builder.button(
        text=texts.BTN_APPROVE,
        callback_data=ReviewAction(review_id=review_id, action="approve"),
    )
    builder.button(
        text=texts.BTN_REQUEST_CHANGES,
        callback_data=ReviewAction(review_id=review_id, action="changes"),
    )
    builder.button(
        text=texts.BTN_FIXED,
        callback_data=ReviewAction(review_id=review_id, action="fixed"),
    )
    builder.button(
        text=texts.BTN_CLOSE,
        callback_data=ReviewAction(review_id=review_id, action="close"),
    )
    builder.adjust(2, 2)
    return builder.as_markup()


def nudge_actions(assignment_id: int, review_url: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if review_url:
        builder.row(InlineKeyboardButton(text=texts.BTN_OPEN_REVIEW, url=review_url))
    builder.row(
        InlineKeyboardButton(
            text=texts.BTN_SNOOZE_HOUR,
            callback_data=SnoozeAction(assignment_id=assignment_id, hours=1).pack(),
        ),
        InlineKeyboardButton(
            text=texts.BTN_SNOOZE_TOMORROW,
            callback_data=SnoozeAction(assignment_id=assignment_id, hours=0).pack(),
        ),
    )
    return builder.as_markup()
