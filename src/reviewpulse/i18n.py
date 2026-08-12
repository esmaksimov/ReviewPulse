"""Which languages the bot speaks, and how a Telegram user maps onto one of them.

Two different things need a locale, and they are not the same lookup:

  * The tracker card and the registration hint live in the shared discussion thread —
    everyone who opens it sees the same message, so there is exactly one locale for
    it: `Settings.default_locale`.
  * DMs (nudges, /start, /status, button-press toasts) are seen by one person, so they
    use that person's own locale: `User.locale` if they set one with /lang, otherwise
    whatever Telegram reports as their client language, otherwise the default.
"""

from __future__ import annotations

SUPPORTED_LOCALES: tuple[str, ...] = ("ru", "en", "es", "it", "zh")

#: Used when nothing else resolves — kept distinct from a *configured* default so a
#: broken DEFAULT_LOCALE in .env fails safe instead of crashing message rendering.
FALLBACK_LOCALE = "en"

#: Telegram's `language_code` is a raw BCP-47 tag ("en-US", "zh-Hans", "pt-BR", ...).
#: Regional variants we don't distinguish collapse onto the base language they are
#: closest to; anything else falls through to the caller's default.
_REGION_ALIASES: dict[str, str] = {
    "zh-hans": "zh",
    "zh-hant": "zh",
    "zh-cn": "zh",
    "zh-sg": "zh",
    "zh-tw": "zh",
    "zh-hk": "zh",
}


def normalize_locale(code: str | None) -> str | None:
    """A user-supplied or Telegram-supplied language tag, or None if unsupported."""
    if not code:
        return None
    lowered = code.strip().lower()
    if lowered in SUPPORTED_LOCALES:
        return lowered
    if lowered in _REGION_ALIASES:
        return _REGION_ALIASES[lowered]
    primary = lowered.split("-")[0]
    return primary if primary in SUPPORTED_LOCALES else None


def resolve_locale(*candidates: str | None, default: str) -> str:
    """The first candidate that maps to a supported locale, else `default`.

    Called as `resolve_locale(user.locale, telegram_user.language_code, default=...)`: an
    explicit /lang choice wins, then Telegram's own client language, then the
    deployment's configured default.
    """
    for candidate in candidates:
        normalized = normalize_locale(candidate)
        if normalized is not None:
            return normalized
    return default if default in SUPPORTED_LOCALES else FALLBACK_LOCALE
