"""Render an `AnnouncementDraft` into the same shape a human would have typed.

Sibling to `card.py`, not `services.announcements`: this module is Telegram/HTML-aware
(goes through `texts.esc()`), the service layer isn't.

The label words below are hand-picked to each match one alternative already inside the
corresponding regex in `parsing.post_parser` — that's what makes the round trip work:
once published, the rendered text is picked up by the same `parse_post` every
hand-typed post goes through, with no special-casing anywhere in the ingestion path.
"Автор:" resolves for free here, unlike a hand-typed post, because the composer's
identity is already known from the DM — there's no opt-in guesswork involved.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from ..db import repo
from ..db.models import AnnouncementDraft
from . import keyboards, texts
from .texts import esc

#: One word per locale, each already an alternative inside the matching regex in
#: `parsing.post_parser` — not part of `texts._STRINGS`, since these are channel-post
#: *content* words, not bot chrome (see `tests/test_announcement.py` for the regex
#: cross-check that keeps the two files from drifting apart).
_REVIEW_LABEL_WORD = {
    "ru": "Ревью",
    "en": "Reviewer",
    "es": "Revisor",
    "it": "Revisori",
    "zh": "评审",
}
_AUTHOR_LABEL_WORD = {
    "ru": "Автор",
    "en": "Author",
    "es": "Autor",
    "it": "Autore",
    "zh": "作者",
}
_DOCS_LABEL_WORD = {
    "ru": "Документация",
    "en": "Docs",
    "es": "Documentación",
    "it": "Documentazione",
    "zh": "文档",
}
_TASK_LABEL_WORD = {"ru": "Задача", "en": "Task", "es": "Tarea", "it": "Attività", "zh": "任务"}
#: The template's fallback for a change with no docs page to link to.
_DESCRIPTION_LABEL_WORD = {
    "ru": "Описание",
    "en": "Description",
    "es": "Descripción",
    "it": "Descrizione",
    "zh": "描述",
}


def render(draft: AnnouncementDraft, locale: str) -> str:
    """The exact text posted to the channel. `locale` is `Settings.default_locale` —
    the channel post has no single owner, same reasoning as `card.render`."""
    lines = [esc(draft.product)]
    if draft.title:
        lines.append(esc(draft.title))

    body = [f"MR: {esc(ref.web_url)}" for ref in repo.draft_merge_requests(draft)]
    if draft.docs_url:
        body.append(f"{_DOCS_LABEL_WORD[locale]}: {esc(draft.docs_url)}")
    if draft.description:
        body.append(f"{_DESCRIPTION_LABEL_WORD[locale]}: {esc(draft.description)}")
    if draft.task_url:
        body.append(f"{_TASK_LABEL_WORD[locale]}: {esc(draft.task_url)}")
    # A review with no MR, no docs and no task is legal (a title plus a description);
    # skipping the separator keeps that post from opening with a stray blank line.
    if body:
        lines.append("")
        lines.extend(body)

    reviewers = [draft.techlead_username, *repo.draft_pool_picks(draft)]
    reviewer_line = " ".join(f"@{esc(name)}" for name in reviewers if name)

    lines.append("")
    lines.append(f"{_REVIEW_LABEL_WORD[locale]}: {reviewer_line}")
    lines.append(f"{_AUTHOR_LABEL_WORD[locale]}: @{esc(draft.composer_username)}")
    return "\n".join(lines)


def render_preview(
    draft: AnnouncementDraft, *, composer_locale: str, channel_locale: str
) -> tuple[str, InlineKeyboardMarkup]:
    """The DM preview: the intro is in the *composer's* locale, the body below it is
    the literal channel-post text (always `channel_locale` = `Settings.default_locale`,
    so what they approve in the preview is exactly what gets posted)."""
    body = render(draft, channel_locale)
    text = f"{texts.t(composer_locale, 'announce_preview_intro')}\n\n{body}"
    has_pool_slot = bool(repo.draft_pool_picks(draft))
    markup = keyboards.announce_preview(draft.id, composer_locale, has_pool_slot=has_pool_slot)
    return text, markup
