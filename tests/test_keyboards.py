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


def test_announce_step_offers_skip_only_where_the_step_is_optional() -> None:
    optional = keyboards.announce_step("en", can_skip=True)
    required = keyboards.announce_step("en", can_skip=False)

    assert [b.text for row in optional.inline_keyboard for b in row] == [
        texts.t("en", "btn_announce_skip"),
        texts.t("en", "btn_announce_cancel"),
    ]
    assert [b.text for row in required.inline_keyboard for b in row] == [
        texts.t("en", "btn_announce_cancel")
    ], "the title is the one thing that cannot be skipped"


def test_announce_products_offers_one_button_per_product_plus_cancel() -> None:
    markup = keyboards.announce_products(["Demo A", "Demo B", "Demo C"], "en")
    assert [b.text for row in markup.inline_keyboard for b in row] == [
        "Demo A",
        "Demo B",
        "Demo C",
        texts.t("en", "btn_announce_cancel"),
    ]


def test_announce_product_buttons_carry_an_index_not_the_name() -> None:
    """callback_data is capped at 64 bytes and product names are non-ASCII."""
    markup = keyboards.announce_products(["Продукт Один", "Продукт Два"], "ru")
    payloads = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert payloads[0] == keyboards.AnnounceProduct(index=0).pack()
    assert all(len(payload.encode()) <= 64 for payload in payloads)


def test_menu_text_sets_cover_every_supported_locale() -> None:
    """The button-tap handlers match on these sets - a locale silently missing from
    one would mean that locale's users tap the button and the bot never responds."""
    for key, text_set in (
        ("btn_menu_status", keyboards.MENU_STATUS_TEXTS),
        ("btn_menu_announce", keyboards.MENU_ANNOUNCE_TEXTS),
        ("btn_menu_stats", keyboards.MENU_STATS_TEXTS),
    ):
        assert text_set == {texts.t(loc, key) for loc in SUPPORTED_LOCALES}
