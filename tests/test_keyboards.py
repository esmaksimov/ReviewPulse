"""Pure keyboard-building helpers: no DB session, no aiogram dispatch needed."""

from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup

from reviewpulse.i18n import SUPPORTED_LOCALES
from reviewpulse.telegram import keyboards, texts


def _labels(markup: ReplyKeyboardMarkup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row]


def test_main_menu_always_has_status_and_announce() -> None:
    markup = keyboards.main_menu("en", show_stats=False)
    assert _labels(markup) == [
        texts.t("en", "btn_menu_status"),
        texts.t("en", "btn_menu_announce"),
    ]


def test_main_menu_adds_stats_only_when_authorized() -> None:
    """Unauthorized users never see the button - `on_stats` still re-checks access
    itself if someone types the label by hand, but the menu shouldn't offer it."""
    markup = keyboards.main_menu("ru", show_stats=True)
    assert _labels(markup) == [
        texts.t("ru", "btn_menu_status"),
        texts.t("ru", "btn_menu_announce"),
        texts.t("ru", "btn_menu_stats"),
    ]


def test_main_menu_is_resized_and_persistent() -> None:
    markup = keyboards.main_menu("en", show_stats=False)
    assert markup.resize_keyboard is True
    assert markup.is_persistent is True


def test_menu_text_sets_cover_every_supported_locale() -> None:
    """The button-tap handlers match on these sets - a locale silently missing from
    one would mean that locale's users tap the button and the bot never responds."""
    for key, text_set in (
        ("btn_menu_status", keyboards.MENU_STATUS_TEXTS),
        ("btn_menu_announce", keyboards.MENU_ANNOUNCE_TEXTS),
        ("btn_menu_stats", keyboards.MENU_STATS_TEXTS),
    ):
        assert text_set == {texts.t(loc, key) for loc in SUPPORTED_LOCALES}
