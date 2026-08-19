"""Parse a review post into the facts the bot needs.

The pinned template and what people actually post have drifted apart — real posts use
"Ревью:" where the template says "Ревьювер:", carry "Задача:" instead of "Документация:",
and split the MR line into "MR SC:" / "MR Utils:". So this parser goes by shape rather
than by the template: labelled lines where they exist, positional fallbacks otherwise.

Label words are matched in every language the bot itself speaks (see `i18n.py`) —
"Ревью:", "Review:", "Revisor:", "Revisori:" and "评审:" are all recognized as the
reviewer line, so a team writing posts in Spanish or Chinese gets the same parsing a
Russian-speaking team does. Only the *labels* are multilingual; free text (the
product name, the task title) is read as-is regardless of language.

It never raises on malformed input. A post it cannot fully understand still produces a
ParsedPost — with `reviewers` empty, which the caller surfaces in the card instead of
silently nudging nobody.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from .gitlab_url import MergeRequestRef, find_merge_requests

#: Telegram usernames: 5-32 chars, must start with a letter. The lookbehind keeps us
#: out of e-mails and URLs.
_USERNAME = re.compile(r"(?<![\w@/.])@([A-Za-z][A-Za-z0-9_]{3,31})\b")

# One alternation per label, ru | en | es | it | zh. Italian largely reuses English
# tech vocabulary ("task", "review") on the ground, so its own words are added
# alongside rather than instead of the English ones.
_REVIEWER_WORDS = r"ревью\w*|reviewers?|revisor(?:es)?|revisión|revisor[ei]|评审人?"
_TASK_WORDS = r"задача|таска|карточка|tasks?|tarea|attività|任务"
_DOCS_WORDS = r"документация|дока|docs?|documentation|documentaci[oó]n|documentazione|文档"
_DESCRIPTION_WORDS = r"описание|descriptions?|descripci[oó]n|descrizione|描述"
#: Opt-in: naming yourself here is what lets the bot notify you when a reviewer
#: requests changes and show it in your /status — see `services.reviews._sync_author`.
_AUTHOR_WORDS = r"автор\w*|authors?|autor(?:es)?|autore|作者"

_REVIEWER_LABEL = re.compile(rf"^\s*(?:{_REVIEWER_WORDS})\s*[:\-：]", re.IGNORECASE)
_TASK_LABEL = re.compile(rf"^\s*(?:{_TASK_WORDS})\s*[:\-：]", re.IGNORECASE)
_DOCS_LABEL = re.compile(rf"^\s*(?:{_DOCS_WORDS})\s*[:\-：]", re.IGNORECASE)
_AUTHOR_LABEL = re.compile(rf"^\s*(?:{_AUTHOR_WORDS})\s*[:\-：]", re.IGNORECASE)

#: Lines that are metadata, never the story/task title.
_ANY_LABEL = re.compile(
    rf"^\s*(mr\b|мр\b|{_REVIEWER_WORDS}|{_TASK_WORDS}|{_DOCS_WORDS}|{_DESCRIPTION_WORDS}|"
    rf"{_AUTHOR_WORDS})",
    re.IGNORECASE,
)

_URL_LINE = re.compile(r"^\s*https?://")


class ReviewerMention(BaseModel):
    """A reviewer named in the post.

    `user_id` is only known when Telegram gave us a `text_mention` entity (a user
    without a public username). For plain `@handle` mentions the id is unknown until
    that person talks to the bot — see the registration flow.
    """

    model_config = ConfigDict(frozen=True)

    username: str | None = None
    user_id: int | None = None
    display_name: str | None = None

    @property
    def label(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.display_name or f"id{self.user_id}"

    @property
    def key(self) -> str:
        return f"id:{self.user_id}" if self.user_id else f"un:{(self.username or '').lower()}"


class ParsedPost(BaseModel):
    model_config = ConfigDict(frozen=True)

    product: str | None = None
    title: str | None = None
    task_url: str | None = None
    docs_url: str | None = None
    merge_requests: list[MergeRequestRef] = Field(default_factory=list)
    reviewers: list[ReviewerMention] = Field(default_factory=list)
    #: True when a recognized reviewer-label line was found — a deliberate signal,
    #: unlike `reviewers` on its own, which can come from scanning the whole post for
    #: any @handle when no label line exists (see `_find_reviewers`).
    has_labelled_reviewers: bool = False
    #: Opt-in, from an "Автор:"-style line — unlike `reviewers` there is no fallback
    #: scan for this, since guessing wrong would notify the wrong person.
    author: ReviewerMention | None = None

    @property
    def looks_like_review(self) -> bool:
        """A post is tracked as a review if it names an MR, or explicitly labels its
        reviewers — a docs-only or infra-only change can go through review without
        ever touching a merge request, as long as reviewers were named on purpose.
        A bare @handle floating in ordinary chatter, with neither, is not enough."""
        return bool(self.merge_requests) or (self.has_labelled_reviewers and bool(self.reviewers))


def parse_post(text: str, entities: list | None = None) -> ParsedPost:
    """`entities` are aiogram MessageEntity objects; only `text_mention` is consulted."""
    lines = text.splitlines()
    meaningful = [line for line in lines if line.strip()]

    product = meaningful[0].strip() if meaningful else None
    title = _find_title(meaningful)

    reviewers = _find_reviewers(lines)
    reviewers.extend(_mentions_from_entities(entities or []))

    return ParsedPost(
        product=product,
        title=title,
        task_url=_labelled_url(lines, _TASK_LABEL),
        docs_url=_labelled_url(lines, _DOCS_LABEL),
        merge_requests=find_merge_requests(text),
        reviewers=_dedupe(reviewers),
        has_labelled_reviewers=any(_REVIEWER_LABEL.match(line) for line in lines),
        author=_find_author(lines),
    )


def _find_title(meaningful: list[str]) -> str | None:
    """The first line after the product that is neither a label nor a bare URL."""
    for line in meaningful[1:]:
        stripped = line.strip()
        if _ANY_LABEL.match(stripped) or _URL_LINE.match(stripped):
            continue
        return stripped
    return None


def _find_reviewers(lines: list[str]) -> list[ReviewerMention]:
    """Usernames from the reviewer-labelled line; the whole post is the fallback.

    Teams put prose on that line ("@user1 for backend / @user2 for everything else"),
    so we take every handle on it rather than trying to parse the sentence.
    """
    for index, line in enumerate(lines):
        if not _REVIEWER_LABEL.match(line):
            continue
        block = [line, *_continuation(lines[index + 1 :])]
        found = _USERNAME.findall("\n".join(block))
        if found:
            return [ReviewerMention(username=name) for name in found]

    return [ReviewerMention(username=name) for name in _USERNAME.findall("\n".join(lines))]


def _continuation(rest: list[str]) -> list[str]:
    """Lines belonging to the reviewer block: everything up to the next blank or label."""
    block: list[str] = []
    for line in rest:
        if not line.strip() or _ANY_LABEL.match(line.strip()):
            break
        block.append(line)
    return block


def _find_author(lines: list[str]) -> ReviewerMention | None:
    """The single @handle on an "Автор:"-style line, if there is one.

    Unlike `_find_reviewers`, there is no whole-post fallback: an unlabelled @handle
    could be anyone mentioned in passing, and guessing wrong would DM a stranger every
    time a reviewer requests changes. No handle on the label line — a bare name, say
    — leaves the author unresolved rather than guessing at one.
    """
    for line in lines:
        if not _AUTHOR_LABEL.match(line):
            continue
        found = _USERNAME.findall(line)
        return ReviewerMention(username=found[0]) if found else None
    return None


def _labelled_url(lines: list[str], label: re.Pattern[str]) -> str | None:
    for line in lines:
        if label.match(line):
            match = re.search(r"https?://\S+", line)
            if match:
                return match.group(0).rstrip(".,;)")
    return None


def _mentions_from_entities(entities: list) -> list[ReviewerMention]:
    mentions: list[ReviewerMention] = []
    for entity in entities:
        if getattr(entity, "type", None) != "text_mention":
            continue
        user = getattr(entity, "user", None)
        if user is None:
            continue
        mentions.append(
            ReviewerMention(
                username=getattr(user, "username", None),
                user_id=user.id,
                display_name=getattr(user, "full_name", None),
            )
        )
    return mentions


def _dedupe(mentions: list[ReviewerMention]) -> list[ReviewerMention]:
    """Merge duplicates, preferring the variant that carries a user_id."""
    by_username: dict[str, ReviewerMention] = {}
    by_id: dict[int, ReviewerMention] = {}
    order: list[str] = []
    merged: dict[str, ReviewerMention] = {}

    for mention in mentions:
        existing = None
        if mention.user_id is not None and mention.user_id in by_id:
            existing = by_id[mention.user_id]
        elif mention.username and mention.username.lower() in by_username:
            existing = by_username[mention.username.lower()]

        if existing is None:
            key = f"slot{len(order)}"
            order.append(key)
            merged[key] = mention
        else:
            key = next(k for k, v in merged.items() if v is existing)
            mention = ReviewerMention(
                username=existing.username or mention.username,
                user_id=existing.user_id or mention.user_id,
                display_name=existing.display_name or mention.display_name,
            )
            merged[key] = mention

        if mention.username:
            by_username[mention.username.lower()] = mention
        if mention.user_id is not None:
            by_id[mention.user_id] = mention

    return [merged[key] for key in order]
