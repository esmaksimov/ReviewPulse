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
    """`Settings.review_projects` has no entry for one of the referenced projects."""

    def __init__(self, project_path: str) -> None:
        super().__init__(f"no REVIEW_PROJECTS entry for {project_path!r}")
        self.project_path = project_path


class ConflictingProjectConfigs(Exception):
    """The referenced MRs span projects whose REVIEW_PROJECTS entries disagree.

    Real posts routinely name several MRs across several repos in one announcement
    (`MR API:` / `MR Utils:` / ...) — that's fine as long as every repo involved is
    configured the same way (same product/techlead/pool/reviewer_count).
    If they're not, there is no principled way to pick a winner, so this is raised
    instead of silently using whichever project happened to be named first.
    """

    def __init__(self, base_project: str, conflicting_projects: list[str]) -> None:
        super().__init__(
            f"{base_project!r} disagrees with {conflicting_projects!r} in REVIEW_PROJECTS"
        )
        self.base_project = base_project
        self.conflicting_projects = conflicting_projects


class ProductNotConfigured(Exception):
    """No `REVIEW_PROJECTS` entry carries the product the composer picked."""

    def __init__(self, product: str) -> None:
        super().__init__(f"no REVIEW_PROJECTS entry for product {product!r}")
        self.product = product


class ChannelNotConfigured(Exception):
    """`Settings.channel_id` isn't set — nowhere to publish to."""


def available_products(settings: Settings) -> list[str]:
    """Every distinct product in `REVIEW_PROJECTS`, in configuration order.

    What the composer picks from when a review names no MR at all — an SQL-only fix
    or a docs change still belongs to a product, there is just no repo to infer it
    from.
    """
    products: list[str] = []
    for config in settings.review_projects.values():
        if config.product not in products:
            products.append(config.product)
    return products


def project_for_product(settings: Settings, product: str) -> str | None:
    """A representative `project_path` for `product`, or None if none carries it.

    Every project sharing a product is required to share its whole config anyway —
    that is exactly what `ConflictingProjectConfigs` enforces for a post spanning
    several repos — so the first match is as good as any. Storing a real path (rather
    than leaving it blank for MR-less drafts) keeps `reroll`'s config lookup a plain
    dict hit with no second code path.
    """
    for path, config in settings.review_projects.items():
        if config.product == product:
            return path
    return None


def resolve_projects(merge_requests: list[MergeRequestRef]) -> list[str]:
    """Every distinct project referenced, in order of first appearance.

    A post naming several MRs across several repos is normal (`MR API:` / `MR Utils:` /
    ... on separate lines) — the parser already finds every one of them by URL shape,
    no label needed. Which single `REVIEW_PROJECTS` entry applies when they
    span more than one repo is decided by `create_draft`, not here: this just reports
    what was actually named.
    """
    seen: list[str] = []
    for ref in merge_requests:
        if ref.project_path not in seen:
            seen.append(ref.project_path)
    return seen


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
    product: str | None = None,
    description: str | None = None,
) -> AnnouncementDraft:
    """Resolve the project(s), draw reviewers, persist the draft.

    `parsed.product` is deliberately reinterpreted as the *title* here: the DM has no
    separate product line (product comes from `REVIEW_PROJECTS`, not from anything the
    composer types), so whatever `parse_post` read as the first line is the title.

    `product` is the composer's own pick, and is only consulted when the post names no
    MR to infer one from — a review can legitimately have no merge request at all (an
    SQL-only change, a docs page), and the ingestion side has always tracked such
    posts as long as they name reviewers deliberately.
    """
    if not composer_username:
        raise ComposerHasNoUsername

    project_paths = resolve_projects(parsed.merge_requests)
    if not project_paths:
        if product is None:
            raise NoMergeRequestFound
        path = project_for_product(settings, product)
        if path is None:
            raise ProductNotConfigured(product)
        base_project, base_config = path, settings.review_projects[path]
    else:
        configs: dict[str, ProjectReviewConfig] = {}
        for path in project_paths:
            config = settings.review_projects.get(path)
            if config is None:
                raise ProjectNotConfigured(path)
            configs[path] = config

        base_project, base_config = project_paths[0], configs[project_paths[0]]
        conflicting = [path for path in project_paths[1:] if configs[path] != base_config]
        if conflicting:
            raise ConflictingProjectConfigs(base_project, conflicting)

    techlead, picks = pick_reviewers(base_config, composer_username=composer_username)

    return await repo.create_draft(
        session,
        composer_user_id=composer_user_id,
        composer_username=composer_username,
        chat_id=chat_id,
        project_path=base_project,
        product=base_config.product,
        title=parsed.product,
        task_url=parsed.task_url,
        docs_url=parsed.docs_url,
        description=description,
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
