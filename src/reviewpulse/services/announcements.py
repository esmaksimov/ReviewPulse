"""`/announce`: generate the pinned-template post instead of hand-typing it.

The composer DMs the bot a title + MR link(s) (+ optional docs link); this module
resolves which `ProjectReviewConfig` applies, draws reviewers, and persists an
`AnnouncementDraft` for the preview/reroll/publish flow in
`telegram.handlers.announce`. Rendering the draft back into the actual post text is
`telegram.announcement`'s job, not this module's — this one stays Telegram-unaware,
mirroring `services.reviews`.
"""

from __future__ import annotations

import random

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ProjectReviewConfig, Settings
from ..db import repo
from ..db.models import AnnouncementDraft
from ..parsing.gitlab_url import MergeRequestRef
from ..parsing.post_parser import ParsedPost


class NoMergeRequestFound(Exception):
    """The composer's message named no MR link — nothing to resolve a project from."""


class ComposerHasNoUsername(Exception):
    """No @username means neither the reviewer line nor the author line can name them."""


class ProjectNotConfigured(Exception):
    """`Settings.review_projects` has no entry for the resolved project."""

    def __init__(self, project_path: str) -> None:
        super().__init__(f"no REVIEW_PROJECTS entry for {project_path!r}")
        self.project_path = project_path


class ChannelNotConfigured(Exception):
    """`Settings.channel_id` isn't set — nowhere to publish to."""


def resolve_project(merge_requests: list[MergeRequestRef]) -> str | None:
    """Which `REVIEW_PROJECTS` entry applies to this draft.

    v1 rule: the first MR's project wins. Real posts do sometimes reference more than
    one project (`MR SC:` / `MR Utils:` on separate lines) — this is a known, accepted
    limitation rather than something worth resolving generically right now.
    """
    return merge_requests[0].project_path if merge_requests else None


def pick_reviewers(
    config: ProjectReviewConfig,
    *,
    composer_username: str,
    rng: random.Random | None = None,
) -> tuple[str | None, list[str]]:
    """Techlead (if configured, and not the composer) plus a random pool draw for
    the rest. Returns (techlead_or_None, pool_picks) — `techlead` is never included
    in `pool_picks`, so a reroll knows exactly which slot(s) it owns.

    The composer reviewing their own post is the same template artefact
    `services.reviews._sync_assignments` already guards against — here it just means
    a configured techlead who happens to be the composer doesn't pin their own slot;
    the full count is drawn from the pool instead.
    """
    rng = rng or random
    composer = composer_username.lower()

    techlead = config.techlead
    if techlead is not None and techlead.lower() == composer:
        techlead = None

    excluded = {composer, (techlead or "").lower()}
    candidates = [name for name in config.pool if name.lower() not in excluded]
    needed = config.reviewer_count - (1 if techlead else 0)
    picks = rng.sample(candidates, k=min(max(needed, 0), len(candidates)))
    return techlead, picks


async def create_draft(
    session: AsyncSession,
    *,
    composer_user_id: int,
    composer_username: str | None,
    chat_id: int,
    parsed: ParsedPost,
    settings: Settings,
) -> AnnouncementDraft:
    """Resolve the project, draw reviewers, persist the draft.

    `parsed.product` is deliberately reinterpreted as the *title* here: the DM has no
    separate product line (product comes from `REVIEW_PROJECTS`, not from anything the
    composer types), so whatever `parse_post` read as the first line is the title.
    """
    if not composer_username:
        raise ComposerHasNoUsername

    project_path = resolve_project(parsed.merge_requests)
    if project_path is None:
        raise NoMergeRequestFound

    config = settings.review_projects.get(project_path)
    if config is None:
        raise ProjectNotConfigured(project_path)

    techlead, picks = pick_reviewers(config, composer_username=composer_username)

    return await repo.create_draft(
        session,
        composer_user_id=composer_user_id,
        composer_username=composer_username,
        chat_id=chat_id,
        project_path=project_path,
        product=config.product,
        title=parsed.product,
        task_url=parsed.task_url,
        docs_url=parsed.docs_url,
        merge_requests=parsed.merge_requests,
        techlead_username=techlead,
        pool_pick_usernames=picks,
    )


async def reroll(
    session: AsyncSession, draft: AnnouncementDraft, settings: Settings
) -> AnnouncementDraft:
    """Redraw the non-pinned reviewer slot(s). Repeats across rerolls are fine — no
    "already shown" tracking, the composer just presses it again if unlucky twice."""
    config = settings.review_projects[draft.project_path]
    _, picks = pick_reviewers(config, composer_username=draft.composer_username)
    await repo.set_draft_pool_picks(session, draft, picks)
    return draft


async def publish(
    bot: Bot, session: AsyncSession, draft: AnnouncementDraft, settings: Settings
) -> None:
    """Post the rendered draft to the channel and mark it published.

    Deliberately does not catch Telegram exceptions — the handler decides the
    user-facing message (e.g. the bot not being a channel admin yet), and the draft is
    left unpublished so the composer can just press Publish again once that's fixed.
    """
    if settings.channel_id is None:
        raise ChannelNotConfigured

    from ..telegram import announcement  # local import: avoids a cycle (telegram -> services)

    text = announcement.render(draft, settings.default_locale)
    await bot.send_message(
        chat_id=settings.channel_id, text=text, disable_web_page_preview=True
    )
    await repo.mark_draft_published(session, draft)
