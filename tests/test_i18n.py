"""Locale resolution, and a completeness check over the translation tables."""

from __future__ import annotations

from datetime import UTC

import pytest

from reviewpulse.i18n import SUPPORTED_LOCALES, normalize_locale, resolve_locale
from reviewpulse.telegram import texts
from reviewpulse.telegram.texts import _STRINGS  # the tables under test


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_every_locale_has_every_key(locale: str) -> None:
    """A translator adding a string to one locale and forgetting another must fail
    loudly here, not silently fall back to English in production."""
    reference = set(_STRINGS["en"])
    assert set(_STRINGS[locale]) == reference


def test_every_string_is_non_empty() -> None:
    for locale, table in _STRINGS.items():
        for key, value in table.items():
            assert value.strip(), f"{locale}.{key} is blank"


def test_exact_locale_codes_pass_through() -> None:
    assert normalize_locale("en") == "en"
    assert normalize_locale("RU") == "ru"


def test_region_variants_collapse_to_the_base_language() -> None:
    assert normalize_locale("en-US") == "en"
    assert normalize_locale("pt-BR") is None, "Portuguese is not a supported locale"
    assert normalize_locale("zh-Hans") == "zh"
    assert normalize_locale("zh-TW") == "zh"


def test_unsupported_or_missing_codes_normalize_to_none() -> None:
    assert normalize_locale("fr") is None
    assert normalize_locale("") is None
    assert normalize_locale(None) is None


def test_resolve_locale_prefers_earlier_candidates() -> None:
    """/lang (stored on the user) outranks Telegram's own language_code."""
    assert resolve_locale("es", "en-US", default="ru") == "es"
    assert resolve_locale(None, "it", default="ru") == "it"


def test_resolve_locale_falls_back_to_the_default() -> None:
    assert resolve_locale(None, None, default="es") == "es"
    assert resolve_locale("fr", "de", default="es") == "es"


def test_resolve_locale_falls_back_further_if_the_default_is_bad() -> None:
    """Guards against a corrupted DEFAULT_LOCALE crashing message rendering."""
    assert resolve_locale(None, default="fr") in SUPPORTED_LOCALES


def test_t_falls_back_to_english_for_a_missing_locale() -> None:
    assert texts.t("xx", "btn_approve") == texts.t("en", "btn_approve")


def test_t_formats_placeholders() -> None:
    assert texts.t("en", "card_progress", approvals=1, needed=2) == "Approvals: 1/2"


def test_state_labels_cover_every_reviewer_state() -> None:
    from reviewpulse.domain.state import ReviewerState

    for locale in SUPPORTED_LOCALES:
        for state in ReviewerState:
            assert texts.state_label(locale, state)


def test_status_line_links_the_headline_when_a_url_is_given() -> None:
    from datetime import datetime

    from reviewpulse.domain.state import ReviewerState

    deadline = datetime(2026, 8, 13, 10, 20, tzinfo=UTC)
    line = texts.status_line(
        "en", "Payments", ReviewerState.PENDING, deadline, 3, "https://t.me/c/123/456"
    )
    assert '<a href="https://t.me/c/123/456">Payments</a>' in line


def test_status_line_without_a_url_still_shows_the_headline() -> None:
    from datetime import datetime

    from reviewpulse.domain.state import ReviewerState

    deadline = datetime(2026, 8, 13, 10, 20, tzinfo=UTC)
    line = texts.status_line("en", "Payments", ReviewerState.PENDING, deadline, 3)
    assert "Payments" in line
    assert "<a href" not in line


def test_status_author_line_names_who_asked_for_changes() -> None:
    line = texts.status_author_line("en", "Payments", ["@alice", "@bob"])
    assert "Payments" in line
    assert "@alice, @bob" in line


def test_author_changes_requested_names_the_reviewer_and_links_the_review() -> None:
    message = texts.author_changes_requested(
        "en",
        reviewer="@alice",
        headline="Payments",
        review_url="https://t.me/c/123/456",
        merge_request_urls=["https://git.example.com/x/-/merge_requests/1"],
    )
    assert "@alice" in message
    assert "Payments" in message
    assert "https://t.me/c/123/456" in message
    assert "https://git.example.com/x/-/merge_requests/1" in message
