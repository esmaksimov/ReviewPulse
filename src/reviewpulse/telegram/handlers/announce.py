"""`/announce`: generate the pinned-template post instead of typing it by hand.

DM-only, deliberately: a DM gives a real `from_user`, unlike an anonymous channel
post, so the author is known for free — and because the bot ends up posting the
result itself, it can manage that post afterwards, which it could never do with a
human's own post (see `services.announcements` and `telegram.announcement` for why).

Two ways in, sharing one finish:

* the step-by-step composer — a menu-button tap or a bare `/announce` — which asks
  for one thing per message and takes Skip for every optional one. Each answer being
  its own short message is also what makes hidden links work: "Документация:
  <a>Confluence</a>" carries no visible URL at all, and reading only the text is how
  a docs link silently vanished from a published post.
* the one-shot `/announce <the whole thing>`, for people who already have the text on
  their clipboard. When that text names no MR there is no repo to infer the product
  from, so it hands over to the composer at the product step rather than refusing.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...db import repo
from ...db.models import AnnouncementDraft
from ...i18n import resolve_locale
from ...parsing.gitlab_url import MergeRequestRef
from ...parsing.post_parser import ParsedPost, first_url, parse_post
from ...services import announcements
from .. import announcement, keyboards, texts
from ..keyboards import MENU_ANNOUNCE_TEXTS, AnnounceAction, AnnounceProduct, AnnounceStep

logger = logging.getLogger(__name__)

router = Router(name="announce")
router.message.filter(F.chat.type == "private")


class Compose(StatesGroup):
    """One state per question the composer asks.

    `product` is only ever entered when no MR was given: with one, the product comes
    from `REVIEW_PROJECTS` via the MR's repo, and asking would be a pointless tap.
    `description` likewise only when the docs link was skipped — it is the template's
    own fallback for a change with no page to link to.
    """

    title = State()
    merge_requests = State()
    product = State()
    docs = State()
    description = State()
    task = State()


async def _locale_for(session: AsyncSession, user: User, settings: Settings) -> str:
    row = await repo.upsert_user(
        session, user.id, user.username, user.full_name, user.language_code
    )
    return resolve_locale(row.locale, user.language_code, default=settings.default_locale)


async def _ask(
    message: Message, state: FSMContext, step: State, key: str, locale: str, *, can_skip: bool
) -> None:
    """Move to `step` and prompt for it, remembering which message carries the buttons.

    Only the newest prompt's Skip is honoured (see `on_step_control`): every prompt
    stays in the chat as history, and without that check a Skip tapped on a
    scrolled-up one would silently skip whatever step is current now.
    """
    await state.set_state(step)
    sent = await message.answer(
        texts.t(locale, key), reply_markup=keyboards.announce_step(locale, can_skip=can_skip)
    )
    await state.update_data(prompt_message_id=sent.message_id)


async def _ask_product_or_docs(
    message: Message, state: FSMContext, locale: str, settings: Settings
) -> None:
    """After the MR step: with MRs the product is already known, without them it is
    the one thing the composer has to supply themselves."""
    data = await state.get_data()
    if data.get("merge_requests"):
        await _ask(message, state, Compose.docs, "announce_step_docs", locale, can_skip=True)
        return

    products = announcements.available_products(settings)
    if not products:
        await state.clear()
        await message.answer(texts.t(locale, "announce_no_products"))
        return

    await state.set_state(Compose.product)
    sent = await message.answer(
        texts.t(locale, "announce_step_product"),
        reply_markup=keyboards.announce_products(products, locale),
    )
    await state.update_data(prompt_message_id=sent.message_id)


# --- entry points -------------------------------------------------------------


@router.message(F.text.in_(MENU_ANNOUNCE_TEXTS))
async def on_announce_button(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    """Registered ahead of the step handlers on purpose: tapping the menu button
    mid-compose restarts cleanly rather than being eaten as an answer."""
    locale = await _locale_for(session, message.from_user, settings)
    if not message.from_user.username:
        await state.clear()
        await message.answer(texts.t(locale, "announce_no_username"))
        return
    await state.set_data({})
    await _ask(message, state, Compose.title, "announce_step_title", locale, can_skip=False)


@router.message(Command("announce"))
async def on_announce(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    locale = await _locale_for(session, message.from_user, settings)

    if not message.from_user.username:
        await state.clear()
        await message.answer(texts.t(locale, "announce_no_username"))
        return

    if not command.args:
        await state.set_data({})
        await _ask(message, state, Compose.title, "announce_step_title", locale, can_skip=False)
        return

    # Entity offsets are relative to the whole message, including the "/announce "
    # this slice drops — hence the shift, without which a hidden link would be
    # attributed to the wrong line.
    text = message.text or ""
    offset = len(text) - len(command.args) if text.endswith(command.args) else 0
    parsed = parse_post(command.args, message.entities, entity_offset=offset)

    if not parsed.merge_requests:
        # Nothing to resolve a product from; carry what was typed into the composer
        # rather than refusing outright, which is what people ran into with an
        # SQL-only change.
        await state.set_data(
            {
                "title": parsed.product,
                "docs_url": parsed.docs_url,
                "task_url": parsed.task_url,
            }
        )
        await _ask_product_or_docs(message, state, locale, settings)
        return

    await state.clear()
    await _create_and_preview(
        message,
        session=session,
        settings=settings,
        locale=locale,
        parsed=parsed,
        product=None,
        description=None,
    )


# --- one step per answer ------------------------------------------------------


@router.message(Compose.title, F.text, ~F.text.startswith("/"))
async def on_title(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, message.from_user, settings)
    await state.update_data(title=message.text.strip())
    await _ask(
        message,
        state,
        Compose.merge_requests,
        "announce_step_merge_requests",
        locale,
        can_skip=True,
    )


@router.message(Compose.merge_requests, F.text, ~F.text.startswith("/"))
async def on_merge_requests(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, message.from_user, settings)
    refs = parse_post(message.text, message.entities).merge_requests
    if not refs:
        await _ask(
            message,
            state,
            Compose.merge_requests,
            "announce_step_no_mr",
            locale,
            can_skip=True,
        )
        return

    await state.update_data(merge_requests=[ref.model_dump() for ref in refs])
    await _ask(message, state, Compose.docs, "announce_step_docs", locale, can_skip=True)


@router.message(Compose.docs, F.text, ~F.text.startswith("/"))
async def on_docs(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, message.from_user, settings)
    url = first_url(message.text, message.entities)
    if url is None:
        await _ask(message, state, Compose.docs, "announce_step_no_url", locale, can_skip=True)
        return

    await state.update_data(docs_url=url)
    await _ask(message, state, Compose.task, "announce_step_task", locale, can_skip=True)


@router.message(Compose.description, F.text, ~F.text.startswith("/"))
async def on_description(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, message.from_user, settings)
    await state.update_data(description=message.text.strip())
    await _ask(message, state, Compose.task, "announce_step_task", locale, can_skip=True)


@router.message(Compose.task, F.text, ~F.text.startswith("/"))
async def on_task(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    locale = await _locale_for(session, message.from_user, settings)
    url = first_url(message.text, message.entities)
    if url is None:
        await _ask(message, state, Compose.task, "announce_step_no_url", locale, can_skip=True)
        return

    await state.update_data(task_url=url)
    await _finish(message, state, session, settings, locale)


# --- skip / cancel / product --------------------------------------------------


@router.callback_query(AnnounceStep.filter())
async def on_step_control(
    query: CallbackQuery,
    callback_data: AnnounceStep,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    locale = await _locale_for(session, query.from_user, settings)
    current = await state.get_state()
    data = await state.get_data()

    # No state at all means the compose ended (or the bot restarted, since this state
    # lives in memory); a mismatched id means an older prompt scrolled up the chat.
    if current is None or query.message.message_id != data.get("prompt_message_id"):
        await query.answer(texts.t(locale, "announce_draft_gone"), show_alert=True)
        return

    if callback_data.action == "cancel":
        await state.clear()
        await _strip_buttons(query, texts.t(locale, "announce_cancelled"))
        await query.answer()
        return

    await query.answer()
    await _strip_buttons(query)

    if current == Compose.merge_requests.state:
        await _ask_product_or_docs(query.message, state, locale, settings)
    elif current == Compose.docs.state:
        await _ask(
            query.message,
            state,
            Compose.description,
            "announce_step_description",
            locale,
            can_skip=True,
        )
    elif current == Compose.description.state:
        await _ask(
            query.message, state, Compose.task, "announce_step_task", locale, can_skip=True
        )
    elif current == Compose.task.state:
        await _finish(query.message, state, session, settings, locale, user=query.from_user)


@router.callback_query(AnnounceProduct.filter())
async def on_product_chosen(
    query: CallbackQuery,
    callback_data: AnnounceProduct,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    locale = await _locale_for(session, query.from_user, settings)
    data = await state.get_data()
    if await state.get_state() is None or query.message.message_id != data.get(
        "prompt_message_id"
    ):
        await query.answer(texts.t(locale, "announce_draft_gone"), show_alert=True)
        return

    products = announcements.available_products(settings)
    if not 0 <= callback_data.index < len(products):
        # The configuration changed under a prompt that was already on screen.
        await query.answer(texts.t(locale, "announce_draft_gone"), show_alert=True)
        return

    await query.answer()
    await _strip_buttons(query)
    await state.update_data(product=products[callback_data.index])
    await _ask(query.message, state, Compose.docs, "announce_step_docs", locale, can_skip=True)


# --- building the draft -------------------------------------------------------


async def _finish(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    locale: str,
    user: User | None = None,
) -> None:
    data = await state.get_data()
    await state.clear()
    composer = user or message.from_user

    refs = [MergeRequestRef(**item) for item in data.get("merge_requests", [])]
    if not any(
        (refs, data.get("docs_url"), data.get("task_url"), data.get("description"))
    ):
        await message.answer(texts.t(locale, "announce_nothing_provided"))
        return

    parsed = ParsedPost(
        # `create_draft` reads `.product` as the title — the DM has no product line,
        # the real product comes from REVIEW_PROJECTS.
        product=data.get("title"),
        merge_requests=refs,
        docs_url=data.get("docs_url"),
        task_url=data.get("task_url"),
    )
    await _create_and_preview(
        message,
        session=session,
        settings=settings,
        locale=locale,
        parsed=parsed,
        product=data.get("product"),
        description=data.get("description"),
        composer=composer,
    )


async def _create_and_preview(
    message: Message,
    *,
    session: AsyncSession,
    settings: Settings,
    locale: str,
    parsed: ParsedPost,
    product: str | None,
    description: str | None,
    composer: User | None = None,
) -> None:
    user = composer or message.from_user
    try:
        draft = await announcements.create_draft(
            session,
            composer_user_id=user.id,
            composer_username=user.username,
            chat_id=message.chat.id,
            parsed=parsed,
            settings=settings,
            product=product,
            description=description,
        )
    except announcements.NoMergeRequestFound:
        await message.answer(texts.t(locale, "announce_no_mr"))
        return
    except announcements.ProductNotConfigured:
        await message.answer(texts.t(locale, "announce_no_products"))
        return
    except announcements.ProjectNotConfigured as exc:
        await message.answer(
            texts.t(locale, "announce_project_unconfigured", project=exc.project_path)
        )
        return
    except announcements.ConflictingProjectConfigs as exc:
        await message.answer(
            texts.t(
                locale,
                "announce_conflicting_projects",
                base=exc.base_project,
                others=", ".join(exc.conflicting_projects),
            )
        )
        return

    text, markup = announcement.render_preview(
        draft, composer_locale=locale, channel_locale=settings.default_locale
    )
    sent = await message.answer(text, reply_markup=markup, disable_web_page_preview=True)
    await repo.set_draft_preview_message(session, draft, sent.message_id)


async def _strip_buttons(query: CallbackQuery, text: str | None = None) -> None:
    """Retire a prompt once it has been answered, so it cannot be tapped twice."""
    try:
        if text is not None:
            await query.message.edit_text(text)
        else:
            await query.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            logger.warning("could not retire announce prompt: %s", exc)


# --- the finished draft -------------------------------------------------------


@router.callback_query(AnnounceAction.filter())
async def on_announce_action(
    query: CallbackQuery,
    callback_data: AnnounceAction,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    locale = await _locale_for(session, query.from_user, settings)

    draft = await repo.get_draft(session, callback_data.draft_id)
    if draft is None or draft.published_at is not None or draft.cancelled_at is not None:
        await query.answer(texts.t(locale, "announce_draft_gone"), show_alert=True)
        return
    if draft.composer_user_id != query.from_user.id:
        await query.answer(texts.t(locale, "announce_not_yours"), show_alert=True)
        return

    handler = {"publish": _publish, "reroll": _reroll, "cancel": _cancel}[callback_data.action]
    answer = await handler(session, draft, bot, settings, locale)
    await query.answer(answer, show_alert=False)


async def _publish(
    session: AsyncSession, draft: AnnouncementDraft, bot: Bot, settings: Settings, locale: str
) -> str:
    try:
        await announcements.publish(bot, session, draft, settings)
    except announcements.ChannelNotConfigured:
        return texts.t(locale, "announce_channel_unconfigured")
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("could not publish announcement draft %s: %s", draft.id, exc)
        return texts.t(locale, "announce_publish_failed")

    await _safe_edit(bot, draft, texts.t(locale, "announce_published_body"))
    return texts.t(locale, "announce_published")


async def _reroll(
    session: AsyncSession, draft: AnnouncementDraft, bot: Bot, settings: Settings, locale: str
) -> str:
    draft = await announcements.reroll(session, draft, settings)
    text, markup = announcement.render_preview(
        draft, composer_locale=locale, channel_locale=settings.default_locale
    )
    await _safe_edit(bot, draft, text, markup)
    return texts.t(locale, "announce_rerolled")


async def _cancel(
    session: AsyncSession, draft: AnnouncementDraft, bot: Bot, settings: Settings, locale: str
) -> str:
    await repo.mark_draft_cancelled(session, draft)
    await _safe_edit(bot, draft, texts.t(locale, "announce_cancelled"))
    return texts.t(locale, "announce_cancelled")


async def _safe_edit(bot: Bot, draft: AnnouncementDraft, text: str, markup=None) -> None:
    """Edit the composer's own preview message — legal, the bot authored it.

    Same "no-op edits are fine" tolerance as `card.refresh`: whichever action races
    to update the message first wins, and Telegram rejecting an identical edit is not
    worth surfacing as a real failure.
    """
    try:
        await bot.edit_message_text(
            chat_id=draft.chat_id,
            message_id=draft.preview_message_id,
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            logger.warning("could not update announce preview %s: %s", draft.id, exc)
