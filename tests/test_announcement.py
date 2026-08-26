"""/announce: reviewer selection, draft persistence, and the render/parse round trip
that lets a generated post flow through the same ingestion pipeline as a hand-typed
one with no special-casing."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from reviewpulse.config import ProjectReviewConfig, Settings
from reviewpulse.db import repo
from reviewpulse.parsing import post_parser
from reviewpulse.parsing.gitlab_url import MergeRequestRef
from reviewpulse.parsing.post_parser import parse_post
from reviewpulse.services import announcements
from reviewpulse.services import reviews as review_service
from reviewpulse.telegram import announcement


def make_settings(channel_id: int | None = None, **projects: dict) -> Settings:
    return Settings(
        _env_file=None,
        BOT_TOKEN="t",
        REVIEW_PROJECTS=projects,
        **({"CHANNEL_ID": channel_id} if channel_id is not None else {}),
    )


# --- pick_reviewers (pure) ---------------------------------------------------


def test_pick_reviewers_includes_techlead_and_fills_pool() -> None:
    config = ProjectReviewConfig(product="Demo", techlead="lead", pool=["a", "b", "c"])
    techlead, picks = announcements.pick_reviewers(
        config, composer_username="author", rng=random.Random(1)
    )
    assert techlead == "lead"
    assert len(picks) == 1
    assert picks[0] in {"a", "b", "c"}


def test_pick_reviewers_excludes_composer_from_the_pool() -> None:
    config = ProjectReviewConfig(product="Demo", pool=["a", "Author", "b"], reviewer_count=3)
    _, picks = announcements.pick_reviewers(
        config, composer_username="author", rng=random.Random(1)
    )
    assert "Author" not in picks
    assert set(picks) == {"a", "b"}


def test_pick_reviewers_drops_techlead_slot_when_composer_is_the_techlead() -> None:
    """Case-insensitive: the config might say "Lead", the composer types "lead"."""
    config = ProjectReviewConfig(product="Demo", techlead="Author", pool=["a", "b", "c"])
    techlead, picks = announcements.pick_reviewers(
        config, composer_username="author", rng=random.Random(1)
    )
    assert techlead is None
    assert len(picks) == 2


def test_pick_reviewers_handles_a_pool_smaller_than_the_requested_count() -> None:
    config = ProjectReviewConfig(product="Demo", pool=["a"], reviewer_count=3)
    _, picks = announcements.pick_reviewers(
        config, composer_username="author", rng=random.Random(1)
    )
    assert picks == ["a"]


def test_pick_reviewers_with_no_techlead_draws_the_full_count_from_the_pool() -> None:
    config = ProjectReviewConfig(product="Demo", pool=["a", "b", "c"], reviewer_count=2)
    techlead, picks = announcements.pick_reviewers(
        config, composer_username="author", rng=random.Random(1)
    )
    assert techlead is None
    assert len(picks) == 2


# --- resolve_projects (pure) ---------------------------------------------------


def test_resolve_projects_lists_every_distinct_project_in_order() -> None:
    refs = [
        MergeRequestRef(host="git.example.com", project_path="backend/api", iid=1),
        MergeRequestRef(host="git.example.com", project_path="backend/utils", iid=2),
        MergeRequestRef(host="git.example.com", project_path="backend/api", iid=3),
    ]
    assert announcements.resolve_projects(refs) == ["backend/api", "backend/utils"]


def test_resolve_projects_is_empty_without_any_mr() -> None:
    assert announcements.resolve_projects([]) == []


# --- create_draft --------------------------------------------------------------


async def test_create_draft_fails_closed_without_any_mr_link(session) -> None:
    settings = make_settings()
    post = parse_post("Обновление контроллера\n\nописание без ссылок")
    with pytest.raises(announcements.NoMergeRequestFound):
        await announcements.create_draft(
            session,
            composer_user_id=1,
            composer_username="author",
            chat_id=1,
            parsed=post,
            settings=settings,
        )


async def test_create_draft_fails_closed_without_a_configured_project(session) -> None:
    settings = make_settings()
    post = parse_post("Title\n\nhttps://git.example.com/backend/api/-/merge_requests/1")
    with pytest.raises(announcements.ProjectNotConfigured) as exc_info:
        await announcements.create_draft(
            session,
            composer_user_id=1,
            composer_username="author",
            chat_id=1,
            parsed=post,
            settings=settings,
        )
    assert exc_info.value.project_path == "backend/api"


async def test_create_draft_requires_a_composer_username(session) -> None:
    settings = make_settings(**{"backend/api": {"product": "Demo"}})
    post = parse_post("Title\n\nhttps://git.example.com/backend/api/-/merge_requests/1")
    with pytest.raises(announcements.ComposerHasNoUsername):
        await announcements.create_draft(
            session,
            composer_user_id=1,
            composer_username=None,
            chat_id=1,
            parsed=post,
            settings=settings,
        )


async def test_create_draft_persists_the_resolved_config(session) -> None:
    settings = make_settings(
        **{
            "backend/api": {
                "product": "Demo Product",
                "techlead": "lead",
                "pool": ["pool1", "pool2"],
                "reviewer_count": 2,
            }
        }
    )
    text = "Обновление контроллера\n\nhttps://git.example.com/backend/api/-/merge_requests/1112"
    draft = await announcements.create_draft(
        session,
        composer_user_id=555,
        composer_username="author",
        chat_id=555,
        parsed=parse_post(text),
        settings=settings,
    )

    assert draft.product == "Demo Product"
    assert draft.title == "Обновление контроллера"
    assert draft.project_path == "backend/api"
    assert draft.techlead_username == "lead"
    assert repo.draft_pool_picks(draft)[0] in {"pool1", "pool2"}
    assert [ref.iid for ref in repo.draft_merge_requests(draft)] == [1112]
    assert draft.published_at is None
    assert draft.cancelled_at is None


# --- multiple MRs in one draft ---------------------------------------------------
#
# Real posts routinely name several MRs across several repos in one announcement
# (`MR SC:` / `MR Utils:` / ... on separate lines, no label needed since the parser
# finds every one by URL shape regardless). That's fine as long as every repo
# named is configured identically in REVIEW_PROJECTS — and rejected outright, not
# guessed at, the moment two of them disagree.


async def test_create_draft_succeeds_when_every_referenced_project_shares_a_config(
    session,
) -> None:
    shared = {"product": "Demo Product", "techlead": "lead", "pool": ["pool1", "pool2"]}
    settings = make_settings(
        **{"backend/api_controller": shared, "backend/checkout": shared, "backend/utils": shared}
    )
    text = (
        "Доработка connection pool\n\n"
        "https://git.example.com/backend/api_controller/-/merge_requests/547\n"
        "https://git.example.com/backend/checkout/-/merge_requests/1145\n"
        "https://git.example.com/backend/utils/-/merge_requests/434"
    )
    draft = await announcements.create_draft(
        session,
        composer_user_id=1,
        composer_username="author",
        chat_id=1,
        parsed=parse_post(text),
        settings=settings,
    )

    assert draft.product == "Demo Product"
    assert draft.project_path == "backend/api_controller"  # the first one — representative only
    assert [ref.iid for ref in repo.draft_merge_requests(draft)] == [547, 1145, 434]


async def test_create_draft_rejects_projects_with_disagreeing_configs(session) -> None:
    settings = make_settings(
        **{
            "backend/api_controller": {"product": "Demo A", "techlead": "lead-a", "pool": ["p1"]},
            "backend/checkout": {"product": "Demo B", "techlead": "lead-b", "pool": ["p2"]},
        }
    )
    text = (
        "Title\n\n"
        "https://git.example.com/backend/api_controller/-/merge_requests/1\n"
        "https://git.example.com/backend/checkout/-/merge_requests/2"
    )
    with pytest.raises(announcements.ConflictingProjectConfigs) as exc_info:
        await announcements.create_draft(
            session,
            composer_user_id=1,
            composer_username="author",
            chat_id=1,
            parsed=parse_post(text),
            settings=settings,
        )
    assert exc_info.value.base_project == "backend/api_controller"
    assert exc_info.value.conflicting_projects == ["backend/checkout"]


async def test_create_draft_fails_closed_when_only_one_of_several_projects_is_configured(
    session,
) -> None:
    """The configured one being first must not mask the missing one."""
    settings = make_settings(
        **{"backend/api_controller": {"product": "Demo", "techlead": "lead", "pool": ["p1"]}}
    )
    text = (
        "Title\n\n"
        "https://git.example.com/backend/api_controller/-/merge_requests/1\n"
        "https://git.example.com/backend/unconfigured/-/merge_requests/2"
    )
    with pytest.raises(announcements.ProjectNotConfigured) as exc_info:
        await announcements.create_draft(
            session,
            composer_user_id=1,
            composer_username="author",
            chat_id=1,
            parsed=parse_post(text),
            settings=settings,
        )
    assert exc_info.value.project_path == "backend/unconfigured"


async def test_reroll_redraws_the_pool_but_keeps_the_techlead(session) -> None:
    settings = make_settings(
        **{
            "backend/api": {
                "product": "Demo",
                "techlead": "lead",
                "pool": ["pool1", "pool2", "pool3"],
            }
        }
    )
    text = "Title\n\nhttps://git.example.com/backend/api/-/merge_requests/1"
    draft = await announcements.create_draft(
        session,
        composer_user_id=1,
        composer_username="author",
        chat_id=1,
        parsed=parse_post(text),
        settings=settings,
    )

    rerolled = await announcements.reroll(session, draft, settings)
    assert rerolled.techlead_username == "lead"
    assert repo.draft_pool_picks(rerolled)[0] in {"pool1", "pool2", "pool3"}


# --- publish --------------------------------------------------------------------


@dataclass
class FakeBot:
    sent: list = field(default_factory=list)

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=42)


async def test_publish_sends_the_rendered_post_and_marks_the_draft_published(session) -> None:
    settings = make_settings(
        channel_id=-100123,
        **{"backend/api": {"product": "Demo Product", "techlead": "lead", "pool": ["pool1"]}},
    )
    text = "Title\n\nhttps://git.example.com/backend/api/-/merge_requests/1"
    draft = await announcements.create_draft(
        session,
        composer_user_id=1,
        composer_username="author",
        chat_id=1,
        parsed=parse_post(text),
        settings=settings,
    )

    bot = FakeBot()
    await announcements.publish(bot, session, draft, settings)

    assert draft.published_at is not None
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == -100123
    assert "Demo Product" in bot.sent[0]["text"]


async def test_publish_fails_closed_without_a_configured_channel(session) -> None:
    settings = make_settings(
        **{"backend/api": {"product": "Demo", "techlead": "lead", "pool": ["pool1"]}}
    )
    text = "Title\n\nhttps://git.example.com/backend/api/-/merge_requests/1"
    draft = await announcements.create_draft(
        session,
        composer_user_id=1,
        composer_username="author",
        chat_id=1,
        parsed=parse_post(text),
        settings=settings,
    )

    with pytest.raises(announcements.ChannelNotConfigured):
        await announcements.publish(FakeBot(), session, draft, settings)


# --- render/parse round trip -----------------------------------------------------


async def test_render_then_parse_reproduces_the_same_facts(session) -> None:
    """The contract that makes "no special-casing on ingestion" true: what the
    generator picked is exactly what parse_post reads back out of the rendered text."""
    settings = make_settings(
        **{
            "backend/api": {
                "product": "Demo Product",
                "techlead": "lead",
                "pool": ["pool1", "pool2"],
                "reviewer_count": 2,
            }
        }
    )
    text = "Обновление контроллера\n\nhttps://git.example.com/backend/api/-/merge_requests/1112"
    draft = await announcements.create_draft(
        session,
        composer_user_id=1,
        composer_username="author",
        chat_id=1,
        parsed=parse_post(text),
        settings=settings,
    )

    rendered = announcement.render(draft, "ru")
    reparsed = parse_post(rendered)

    assert reparsed.product == "Demo Product"
    assert reparsed.title == "Обновление контроллера"
    assert [(mr.project_path, mr.iid) for mr in reparsed.merge_requests] == [("backend/api", 1112)]
    assert reparsed.has_labelled_reviewers
    assert {m.username for m in reparsed.reviewers} == {"lead", *repo.draft_pool_picks(draft)}
    assert reparsed.author is not None
    assert reparsed.author.username == "author"
    assert reparsed.looks_like_review


@pytest.mark.parametrize("locale", ["ru", "en", "es", "it", "zh"])
def test_render_labels_match_the_parsers_own_regexes(locale: str) -> None:
    """Guards the two files from drifting apart: each label word `announcement.py`
    writes must actually be recognized by `post_parser`'s compiled regexes."""
    assert post_parser._REVIEWER_LABEL.match(f"{announcement._REVIEW_LABEL_WORD[locale]}: @x")
    assert post_parser._AUTHOR_LABEL.match(f"{announcement._AUTHOR_LABEL_WORD[locale]}: @x")
    assert post_parser._DOCS_LABEL.match(f"{announcement._DOCS_LABEL_WORD[locale]}: http://x")
    assert post_parser._TASK_LABEL.match(f"{announcement._TASK_LABEL_WORD[locale]}: http://x")


async def test_generated_post_ingests_the_same_way_a_human_post_would(session) -> None:
    """One level deeper than the round-trip test: actually run it through
    services.reviews.create_or_update_review, the same entry point on_channel_post
    uses for a hand-typed post."""
    settings = make_settings(
        **{"backend/api": {"product": "Demo Product", "techlead": "lead", "pool": ["pool1"]}}
    )
    text = "Title\n\nhttps://git.example.com/backend/api/-/merge_requests/1"
    draft = await announcements.create_draft(
        session,
        composer_user_id=1,
        composer_username="author",
        chat_id=1,
        parsed=parse_post(text),
        settings=settings,
    )

    rendered = announcement.render(draft, "ru")
    parsed = parse_post(rendered)
    review = await review_service.create_or_update_review(
        session,
        channel_chat_id=-100999,
        channel_message_id=1,
        post=parsed,
        raw_text=rendered,
        posted_at=draft.created_at,
    )

    assert review.author_username == "author"
    assert {row.username for row in review.assignments} == {"lead", "pool1"}
