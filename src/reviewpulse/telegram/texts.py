"""All user-facing copy, in every supported language.

Two audiences need different locales (see `i18n.py`): the shared card and the
registration hint use `Settings.default_locale`, everything else — DMs, command
replies, button-press toasts — uses the individual reviewer's own locale.

Strings live in `_STRINGS[locale][key]`, fetched through `t()`. A handful of composite
messages (the card, a nudge, a status line) are assembled by dedicated functions that
call `t()` for each piece and stitch them together with the runtime data.

Every message here is sent with `parse_mode=HTML` (see `telegram.bot.build_bot`), so
the templates own their markup and *every* runtime value spliced into them must go
through `esc()` first. Skipping it is not a cosmetic bug: a post titled
"<Название продукта>" made Telegram reject the whole message with "can't parse
entities", which reads to the user as the bot silently ignoring /status.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..domain.escalation import NudgeReason
from ..domain.state import ReviewerState
from ..i18n import FALLBACK_LOCALE


def esc(value: object) -> str:
    """Escape one runtime value (a post title, an @handle, a URL) for HTML mode.

    Telegram's HTML parser recognises exactly four entities — `&lt;`, `&gt;`, `&amp;`
    and `&quot;` — so those are what we replace, and nothing else. `html.escape` is
    deliberately not used: with `quote=True` it also emits `&#x27;` for apostrophes,
    which Telegram does not decode and would render literally in ordinary prose.

    Safe in both text and attribute position, so `href="{esc(url)}"` is covered too.
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

#: Native name of each language, for the /lang confirmation and its usage hint.
LANGUAGE_NAMES: dict[str, str] = {
    "ru": "Русский",
    "en": "English",
    "es": "Español",
    "it": "Italiano",
    "zh": "中文",
}

_STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        "btn_approve": "👍 Апрув",
        "btn_request_changes": "✍️ Поправить",
        "btn_fixed": "✅ Поправил",
        "btn_close": "🗄 Закрыть",
        "btn_snooze_hour": "🔕 Час",
        "btn_snooze_tomorrow": "🔕 До завтра",
        "btn_open_review": "Открыть ревью",
        "btn_claim": "🙋 Я ревьювер",
        "state_pending": "⏳ ждёт ревью",
        "state_changes_requested": "✍️ ждёт правок от автора",
        "state_awaiting_recheck": "🔁 правки готовы, ждёт повторного взгляда",
        "state_approved": "👍 апрув",
        "default_headline": "Ревью",
        "card_closed": "✅ <b>Закрыто</b> — {approvals}/{needed}",
        "card_progress": "Апрувы: {approvals}/{needed}",
        "card_unparsed_reviewers": (
            "⚠️ Не смог определить ревьюверов из поста. "
            "Нажмите кнопку ниже, чтобы взять ревью на себя."
        ),
        "nudge_stale_title": "🔁 <b>Замечания закрыты, а ✍️ висит</b>",
        "nudge_stale_body": "Автор поправил всё, что ты просил, но апрува от тебя пока нет.",
        "nudge_pending_title": "⏳ <b>На тебе висит ревью</b>",
        "nudge_pending_body": "Ты ещё не поставил вердикт.",
        "nudge_overdue": "Просрочка: {duration} рабочего времени",
        "nudge_open_discussion": "Открыть обсуждение",
        "author_changes_requested_title": "✍️ <b>Просят правки</b>",
        "author_changes_requested_body": "{reviewer} оставил(а) замечания по твоему ревью.",
        "unit_minute": "мин",
        "unit_hour": "ч",
        "status_header": "<b>Ревью на тебе</b>",
        "status_author_header": "<b>Ревью, где правки просят у тебя</b>",
        "status_line_deadline": "дедлайн",
        "status_author_line_suffix": "правки просят: {who}",
        "nothing_pending": "На тебе ничего не висит. 🎉",
        "start_message": (
            "Привет! Я слежу за ревью в канале и напоминаю, когда мяч на твоей стороне.\n\n"
            "Теперь я знаю, как тебе написать — если на тебе повиснет ревью, пришлю "
            "напоминание в рабочее время.\n\n"
            "Команды:\n"
            "/status — что висит на мне\n"
            "/announce — собрать пост для канала за меня\n"
            "/link &lt;gitlab-логин&gt; — связать с аккаунтом GitLab\n"
            "/lang &lt;код&gt; — сменить язык бота\n"
            "/mute 2h — не беспокоить\n"
            "/unmute — снова беспокоить"
        ),
        "start_found_open": "\n\nНашёл открытых ревью на тебе: {count}. Посмотреть — /status",
        "start_found_authored": (
            "\n\nТеперь буду уведомлять о правках по твоим ревью ({count}). Посмотреть — /status"
        ),
        "no_username": (
            "У тебя не задан @username в Telegram. Я узнаю ревьюверов по нику из поста, "
            "так что без него не смогу связать тебя с ревью — задай ник в настройках "
            "Telegram и напиши мне /start ещё раз."
        ),
        "link_usage": "Формат: <code>/link ivanov</code>",
        "link_done": "Связал с GitLab: <code>{login}</code>",
        "mute_usage": "Формат: <code>/mute 2h</code> или <code>/mute 1d</code>",
        "mute_done": "Молчу {duration}. Вернуть — /unmute",
        "unmute_done": "Снова напоминаю.",
        "lang_usage": "Формат: <code>/lang en</code>. Доступно: {list}",
        "lang_done": "Язык бота: {name}.",
        "registration_hint": (
            "{who} — я не могу писать вам в личку, пока вы не начнёте со мной диалог. "
            "Откройте @{bot} и нажмите /start, иначе напоминания по этому ревью "
            "до вас не дойдут."
        ),
        "not_a_reviewer": "Ты не назначен ревьювером на это ревью.",
        "review_gone": "Не нахожу это ревью — возможно, оно уже закрыто.",
        "answer_approved": "👍 Апрув засчитан",
        "answer_changes": "✍️ Отмечено: нужны правки",
        "answer_fixed": "Ревьюверы уведомлены, что правки готовы",
        "answer_already": "Уже {state}",
        "answer_closed_by_approval": "👍 Апрув засчитан, ревью закрыто",
        "answer_nothing_to_fix": "Никто сейчас не ждёт правок по этому ревью",
        "answer_review_closed": "Ревью закрыто",
        "answer_already_closed": "Ревью уже закрыто",
        "answer_already_reviewer": "Ты уже ревьювер этого ревью",
        "answer_now_reviewer": "Теперь ты ревьювер этого ревью",
        "snoozed_hour": "Не побеспокою 1 ч",
        "snoozed_tomorrow": "Не побеспокою до завтра",
        "announce_no_username": (
            "У тебя не задан @username в Telegram — без него некого будет "
            "подставить в «Автор:». Задай ник в настройках Telegram и попробуй снова."
        ),
        "announce_no_mr": "Не нашёл ссылку на MR в сообщении.",
        "announce_project_unconfigured": (
            "Проект «{project}» не настроен для /announce — попроси добавить его в REVIEW_PROJECTS."
        ),
        "announce_conflicting_projects": (
            "Проект «{base}» настроен иначе, чем {others} — смешивать проекты в одном "
            "/announce можно, только если у них одинаковый REVIEW_PROJECTS (продукт/"
            "техлид/пул). Разбей на отдельные посты или приведи их конфиг к одному виду."
        ),
        "announce_preview_intro": "Вот что получится — проверь и опубликуй, если всё верно.",
        "btn_announce_publish": "✅ Опубликовать",
        "btn_announce_reroll": "🔁 Другой ревьювер",
        "btn_announce_cancel": "❌ Отмена",
        "btn_menu_status": "📋 Статус",
        "btn_menu_announce": "📢 Анонс",
        "btn_menu_stats": "📊 Статистика",
        "btn_announce_skip": "⏭ Пропустить",
        "announce_step_title": "Как называется задача? Пришли одной строкой.",
        "announce_step_merge_requests": (
            "Ссылки на MR/PR — можно несколько, каждая с новой строки. "
            "Если их нет, жми «Пропустить»."
        ),
        "announce_step_product": "К какому продукту относится задача?",
        "announce_step_docs": "Ссылка на документацию? Или «Пропустить».",
        "announce_step_description": (
            "Тогда опиши задачу в двух словах — это уйдёт в пост строкой «Описание:». "
            "Или «Пропустить»."
        ),
        "announce_step_task": "Ссылка на задачу? Или «Пропустить».",
        "announce_step_no_mr": (
            "Не вижу тут ссылок на MR/PR. Пришли ещё раз или нажми «Пропустить»."
        ),
        "announce_step_no_url": "Не вижу тут ссылки. Пришли ещё раз или нажми «Пропустить».",
        "announce_nothing_provided": (
            "Пост не из чего собрать: ни MR, ни документации, ни задачи, ни описания. "
            "Начни заново — /announce."
        ),
        "announce_no_products": (
            "Ни один проект не настроен для /announce — попроси добавить их в REVIEW_PROJECTS."
        ),
        "announce_draft_gone": (
            "Не нахожу этот черновик — возможно, он уже опубликован или отменён."
        ),
        "announce_not_yours": "Это чужой черновик.",
        "announce_channel_unconfigured": "У бота не настроен CHANNEL_ID — некуда публиковать.",
        "announce_publish_failed": (
            "Не смог опубликовать в канал — проверь, что бот админ канала с правом постить."
        ),
        "announce_published": "Опубликовано ✅",
        "announce_published_body": "✅ <b>Опубликовано в канал.</b>",
        "announce_rerolled": "Выбрал другого ревьювера",
        "announce_cancelled": "Черновик отменён",
        "stats_report_title": "📊 <b>Статистика ревью: {since}–{until}</b>",
        "stats_report_empty": "За этот период данных нет.",
        "stats_fix_time_header": "<b>Дольше всех чинят правки после ✍️:</b>",
        "stats_fix_time_empty": "Правок за этот период не было.",
        "stats_response_time_header": "<b>Дольше всех реагируют на новое ревью:</b>",
        "stats_response_time_empty": "Новых вердиктов за этот период не было.",
        "stats_person_line": "• {label} — медиана {median} ({count})",
        "stats_command_no_access": "Эта команда тебе недоступна.",
    },
    "en": {
        "btn_approve": "👍 Approve",
        "btn_request_changes": "✍️ Request changes",
        "btn_fixed": "✅ Fixed",
        "btn_close": "🗄 Close",
        "btn_snooze_hour": "🔕 1h",
        "btn_snooze_tomorrow": "🔕 Tomorrow",
        "btn_open_review": "Open review",
        "btn_claim": "🙋 I'm a reviewer",
        "state_pending": "⏳ awaiting review",
        "state_changes_requested": "✍️ waiting on the author's fixes",
        "state_awaiting_recheck": "🔁 fixes are in, awaiting another look",
        "state_approved": "👍 approved",
        "default_headline": "Review",
        "card_closed": "✅ <b>Closed</b> — {approvals}/{needed}",
        "card_progress": "Approvals: {approvals}/{needed}",
        "card_unparsed_reviewers": (
            "⚠️ Couldn't work out the reviewers from this post. "
            "Tap the button below to take the review yourself."
        ),
        "nudge_stale_title": "🔁 <b>Fixes are in, but ✍️ still stands</b>",
        "nudge_stale_body": (
            "The author addressed everything you asked for, but there's no approval "
            "from you yet."
        ),
        "nudge_pending_title": "⏳ <b>A review is waiting on you</b>",
        "nudge_pending_body": "You haven't given a verdict yet.",
        "nudge_overdue": "Overdue by: {duration} of working time",
        "nudge_open_discussion": "Open discussion",
        "author_changes_requested_title": "✍️ <b>Changes requested</b>",
        "author_changes_requested_body": "{reviewer} left feedback on your review.",
        "unit_minute": "min",
        "unit_hour": "h",
        "status_header": "<b>Reviews waiting on you</b>",
        "status_author_header": "<b>Reviews waiting on your fixes</b>",
        "status_line_deadline": "due",
        "status_author_line_suffix": "changes requested by: {who}",
        "nothing_pending": "Nothing is waiting on you. 🎉",
        "start_message": (
            "Hi! I keep an eye on reviews in the channel and DM whoever the ball is on.\n\n"
            "I now know how to reach you — if a review lands on you, I'll send a "
            "reminder during working hours.\n\n"
            "Commands:\n"
            "/status — what's waiting on you\n"
            "/announce — put together the channel post for you\n"
            "/link &lt;gitlab-username&gt; — link your GitLab account\n"
            "/lang &lt;code&gt; — change the bot's language\n"
            "/mute 2h — go quiet\n"
            "/unmute — start reminding again"
        ),
        "start_found_open": "\n\nFound open reviews on you: {count}. Check them — /status",
        "start_found_authored": (
            "\n\nI'll now notify you about changes on your reviews ({count}). Check — /status"
        ),
        "no_username": (
            "You don't have a @username set in Telegram. I match reviewers by the "
            "handle in the post, so without one I can't link you to a review — set a "
            "username in Telegram's settings and send me /start again."
        ),
        "link_usage": "Usage: <code>/link ivanov</code>",
        "link_done": "Linked to GitLab: <code>{login}</code>",
        "mute_usage": "Usage: <code>/mute 2h</code> or <code>/mute 1d</code>",
        "mute_done": "Quiet for {duration}. Resume with /unmute",
        "unmute_done": "Reminding you again.",
        "lang_usage": "Usage: <code>/lang en</code>. Available: {list}",
        "lang_done": "Bot language: {name}.",
        "registration_hint": (
            "{who} — I can't DM you until you start a conversation with me. Open "
            "@{bot} and press /start, otherwise reminders for this review won't "
            "reach you."
        ),
        "not_a_reviewer": "You're not assigned as a reviewer on this review.",
        "review_gone": "Can't find this review — it may already be closed.",
        "answer_approved": "👍 Approval recorded",
        "answer_changes": "✍️ Marked: changes requested",
        "answer_fixed": "Reviewers notified the fixes are ready",
        "answer_already": "Already {state}",
        "answer_closed_by_approval": "👍 Approval recorded, review closed",
        "answer_nothing_to_fix": "No one is currently waiting on fixes for this review",
        "answer_review_closed": "Review closed",
        "answer_already_closed": "Review is already closed",
        "answer_already_reviewer": "You're already a reviewer on this review",
        "answer_now_reviewer": "You're now a reviewer on this review",
        "snoozed_hour": "Quiet for 1h",
        "snoozed_tomorrow": "Quiet until tomorrow",
        "announce_no_username": (
            "You don't have a @username set in Telegram — without one there's nobody "
            "to put in \"Author:\". Set a username in Telegram's settings and try again."
        ),
        "announce_no_mr": "No merge-request link found in that message.",
        "announce_project_unconfigured": (
            'Project "{project}" isn\'t set up for /announce — ask for it to be '
            "added to REVIEW_PROJECTS."
        ),
        "announce_conflicting_projects": (
            'Project "{base}" is configured differently than {others} — projects can '
            "only be mixed in one /announce if their REVIEW_PROJECTS entries match "
            "(product/techlead/pool). Split into separate posts, or align their configs."
        ),
        "announce_preview_intro": (
            "Here's what this will look like — check it and publish if it's right."
        ),
        "btn_announce_publish": "✅ Publish",
        "btn_announce_reroll": "🔁 Reroll reviewer",
        "btn_announce_cancel": "❌ Cancel",
        "btn_menu_status": "📋 Status",
        "btn_menu_announce": "📢 Announce",
        "btn_menu_stats": "📊 Stats",
        "btn_announce_skip": "⏭ Skip",
        "announce_step_title": "What is the task called? One line is enough.",
        "announce_step_merge_requests": (
            "MR/PR links — several are fine, one per line. Tap Skip if there aren't any."
        ),
        "announce_step_product": "Which product is this for?",
        "announce_step_docs": "A docs link? Or tap Skip.",
        "announce_step_description": (
            "Then describe it in a sentence — it goes into the post as a "
            "«Description:» line. Or tap Skip."
        ),
        "announce_step_task": "A task link? Or tap Skip.",
        "announce_step_no_mr": "No MR/PR links in there. Send them again, or tap Skip.",
        "announce_step_no_url": "No link in there. Send it again, or tap Skip.",
        "announce_nothing_provided": (
            "There's nothing to build a post from: no MR, no docs, no task, no "
            "description. Start again with /announce."
        ),
        "announce_no_products": (
            "No project is set up for /announce — ask for them to be added to REVIEW_PROJECTS."
        ),
        "announce_draft_gone": (
            "Can't find that draft — it may already be published or cancelled."
        ),
        "announce_not_yours": "This isn't your draft.",
        "announce_channel_unconfigured": "The bot has no CHANNEL_ID set — nowhere to publish to.",
        "announce_publish_failed": (
            "Couldn't publish to the channel — check that the bot is a channel "
            "admin with post rights."
        ),
        "announce_published": "Published ✅",
        "announce_published_body": "✅ <b>Published to the channel.</b>",
        "announce_rerolled": "Drew a different reviewer",
        "announce_cancelled": "Draft cancelled",
        "stats_report_title": "📊 <b>Review stats: {since}–{until}</b>",
        "stats_report_empty": "No data for this period.",
        "stats_fix_time_header": "<b>Slowest to fix after changes requested:</b>",
        "stats_fix_time_empty": "No fixes completed this period.",
        "stats_response_time_header": "<b>Slowest to give a first verdict:</b>",
        "stats_response_time_empty": "No first verdicts this period.",
        "stats_person_line": "• {label} — median {median} ({count})",
        "stats_command_no_access": "This command isn't available to you.",
    },
    "es": {
        "btn_approve": "👍 Aprobar",
        "btn_request_changes": "✍️ Solicitar cambios",
        "btn_fixed": "✅ Corregido",
        "btn_close": "🗄 Cerrar",
        "btn_snooze_hour": "🔕 1 h",
        "btn_snooze_tomorrow": "🔕 Mañana",
        "btn_open_review": "Abrir revisión",
        "btn_claim": "🙋 Soy revisor",
        "state_pending": "⏳ pendiente de revisión",
        "state_changes_requested": "✍️ esperando cambios del autor",
        "state_awaiting_recheck": "🔁 cambios listos, esperando una nueva revisión",
        "state_approved": "👍 aprobado",
        "default_headline": "Revisión",
        "card_closed": "✅ <b>Cerrado</b> — {approvals}/{needed}",
        "card_progress": "Aprobaciones: {approvals}/{needed}",
        "card_unparsed_reviewers": (
            "⚠️ No pude identificar a los revisores en esta publicación. "
            "Pulsa el botón de abajo para asignarte la revisión."
        ),
        "nudge_stale_title": "🔁 <b>Los cambios están listos, pero ✍️ sigue puesto</b>",
        "nudge_stale_body": (
            "El autor corrigió todo lo que pediste, pero aún no diste tu aprobación."
        ),
        "nudge_pending_title": "⏳ <b>Tienes una revisión pendiente</b>",
        "nudge_pending_body": "Todavía no diste tu veredicto.",
        "nudge_overdue": "Retraso: {duration} de tiempo laboral",
        "nudge_open_discussion": "Abrir discusión",
        "author_changes_requested_title": "✍️ <b>Se solicitaron cambios</b>",
        "author_changes_requested_body": "{reviewer} dejó comentarios en tu revisión.",
        "unit_minute": "min",
        "unit_hour": "h",
        "status_header": "<b>Revisiones pendientes de ti</b>",
        "status_author_header": "<b>Revisiones que esperan tus cambios</b>",
        "status_line_deadline": "plazo",
        "status_author_line_suffix": "cambios solicitados por: {who}",
        "nothing_pending": "No tienes nada pendiente. 🎉",
        "start_message": (
            "¡Hola! Sigo las revisiones del canal y aviso por privado a quien tenga "
            "el turno.\n\n"
            "Ya sé cómo escribirte — si una revisión queda en tu lado, te enviaré un "
            "recordatorio en horario laboral.\n\n"
            "Comandos:\n"
            "/status — qué tienes pendiente\n"
            "/announce — preparo la publicación del canal por ti\n"
            "/link &lt;usuario-gitlab&gt; — vincular tu cuenta de GitLab\n"
            "/lang &lt;código&gt; — cambiar el idioma del bot\n"
            "/mute 2h — silenciar\n"
            "/unmute — volver a avisar"
        ),
        "start_found_open": "\n\nEncontré revisiones pendientes en ti: {count}. Míralas — /status",
        "start_found_authored": (
            "\n\nAhora te avisaré de los cambios en tus revisiones ({count}). Mira — /status"
        ),
        "no_username": (
            "No tienes un @username configurado en Telegram. Identifico a los "
            "revisores por el usuario que aparece en la publicación, así que sin uno "
            "no puedo vincularte a una revisión — configura un usuario en los ajustes "
            "de Telegram y envíame /start de nuevo."
        ),
        "link_usage": "Formato: <code>/link ivanov</code>",
        "link_done": "Vinculado a GitLab: <code>{login}</code>",
        "mute_usage": "Formato: <code>/mute 2h</code> o <code>/mute 1d</code>",
        "mute_done": "Silenciado por {duration}. Reanudar con /unmute",
        "unmute_done": "Vuelvo a avisarte.",
        "lang_usage": "Formato: <code>/lang en</code>. Disponibles: {list}",
        "lang_done": "Idioma del bot: {name}.",
        "registration_hint": (
            "{who} — no puedo escribirles por privado hasta que inicien una "
            "conversación conmigo. Abran @{bot} y pulsen /start, de lo contrario los "
            "recordatorios de esta revisión no les llegarán."
        ),
        "not_a_reviewer": "No estás asignado como revisor en esta revisión.",
        "review_gone": "No encuentro esta revisión — puede que ya esté cerrada.",
        "answer_approved": "👍 Aprobación registrada",
        "answer_changes": "✍️ Marcado: se solicitaron cambios",
        "answer_fixed": "Se notificó a los revisores que los cambios están listos",
        "answer_already": "Ya estaba {state}",
        "answer_closed_by_approval": "👍 Aprobación registrada, revisión cerrada",
        "answer_nothing_to_fix": "Nadie está esperando cambios en esta revisión ahora mismo",
        "answer_review_closed": "Revisión cerrada",
        "answer_already_closed": "La revisión ya estaba cerrada",
        "answer_already_reviewer": "Ya eres revisor de esta revisión",
        "answer_now_reviewer": "Ahora eres revisor de esta revisión",
        "snoozed_hour": "Silenciado 1 h",
        "snoozed_tomorrow": "Silenciado hasta mañana",
        "announce_no_username": (
            "No tienes un @username configurado en Telegram — sin uno no hay a quién "
            "poner en \"Autor:\". Configura un usuario en Telegram e inténtalo de nuevo."
        ),
        "announce_no_mr": "No encontré ningún enlace a un MR en ese mensaje.",
        "announce_project_unconfigured": (
            "El proyecto «{project}» no está configurado para /announce — pide "
            "que lo añadan a REVIEW_PROJECTS."
        ),
        "announce_conflicting_projects": (
            "El proyecto «{base}» está configurado de forma distinta a {others} — solo "
            "se pueden mezclar proyectos en un /announce si su entrada en "
            "REVIEW_PROJECTS coincide (producto/técnico líder/grupo). Divide en "
            "publicaciones separadas o alinea su configuración."
        ),
        "announce_preview_intro": "Así quedaría — revísalo y publica si está bien.",
        "btn_announce_publish": "✅ Publicar",
        "btn_announce_reroll": "🔁 Otro revisor",
        "btn_announce_cancel": "❌ Cancelar",
        "btn_menu_status": "📋 Estado",
        "btn_menu_announce": "📢 Anuncio",
        "btn_menu_stats": "📊 Estadísticas",
        "btn_announce_skip": "⏭ Omitir",
        "announce_step_title": "¿Cómo se llama la tarea? Con una línea basta.",
        "announce_step_merge_requests": (
            "Enlaces de MR/PR — pueden ser varios, uno por línea. "
            "Pulsa Omitir si no hay ninguno."
        ),
        "announce_step_product": "¿A qué producto pertenece?",
        "announce_step_docs": "¿Un enlace a la documentación? O pulsa Omitir.",
        "announce_step_description": (
            "Entonces descríbela en una frase — irá al post como línea "
            "«Descripción:». O pulsa Omitir."
        ),
        "announce_step_task": "¿Un enlace a la tarea? O pulsa Omitir.",
        "announce_step_no_mr": "No veo enlaces de MR/PR. Envíalos de nuevo o pulsa Omitir.",
        "announce_step_no_url": "No veo ningún enlace. Envíalo de nuevo o pulsa Omitir.",
        "announce_nothing_provided": (
            "No hay con qué armar el post: ni MR, ni documentación, ni tarea, ni "
            "descripción. Empieza de nuevo con /announce."
        ),
        "announce_no_products": (
            "Ningún proyecto está configurado para /announce — pide que los añadan "
            "a REVIEW_PROJECTS."
        ),
        "announce_draft_gone": (
            "No encuentro ese borrador — puede que ya esté publicado o cancelado."
        ),
        "announce_not_yours": "Este borrador no es tuyo.",
        "announce_channel_unconfigured": (
            "El bot no tiene CHANNEL_ID configurado — no hay dónde publicar."
        ),
        "announce_publish_failed": (
            "No pude publicar en el canal — comprueba que el bot es admin del "
            "canal con permiso para publicar."
        ),
        "announce_published": "Publicado ✅",
        "announce_published_body": "✅ <b>Publicado en el canal.</b>",
        "announce_rerolled": "Elegí otro revisor",
        "announce_cancelled": "Borrador cancelado",
        "stats_report_title": "📊 <b>Estadísticas de revisión: {since}–{until}</b>",
        "stats_report_empty": "No hay datos para este período.",
        "stats_fix_time_header": "<b>Más lentos en corregir tras solicitar cambios:</b>",
        "stats_fix_time_empty": "No hubo correcciones en este período.",
        "stats_response_time_header": "<b>Más lentos en dar un primer veredicto:</b>",
        "stats_response_time_empty": "No hubo primeros veredictos en este período.",
        "stats_person_line": "• {label} — mediana {median} ({count})",
        "stats_command_no_access": "Este comando no está disponible para ti.",
    },
    "it": {
        "btn_approve": "👍 Approva",
        "btn_request_changes": "✍️ Richiedi modifiche",
        "btn_fixed": "✅ Corretto",
        "btn_close": "🗄 Chiudi",
        "btn_snooze_hour": "🔕 1 h",
        "btn_snooze_tomorrow": "🔕 Domani",
        "btn_open_review": "Apri la review",
        "btn_claim": "🙋 Sono un revisore",
        "state_pending": "⏳ in attesa di review",
        "state_changes_requested": "✍️ in attesa delle modifiche dell'autore",
        "state_awaiting_recheck": "🔁 modifiche pronte, in attesa di un nuovo controllo",
        "state_approved": "👍 approvato",
        "default_headline": "Review",
        "card_closed": "✅ <b>Chiuso</b> — {approvals}/{needed}",
        "card_progress": "Approvazioni: {approvals}/{needed}",
        "card_unparsed_reviewers": (
            "⚠️ Non sono riuscito a capire chi sono i revisori da questo post. "
            "Tocca il pulsante qui sotto per assegnarti la review."
        ),
        "nudge_stale_title": "🔁 <b>Le modifiche sono pronte, ma ✍️ è ancora attivo</b>",
        "nudge_stale_body": (
            "L'autore ha sistemato tutto quello che avevi chiesto, ma non hai ancora "
            "dato l'approvazione."
        ),
        "nudge_pending_title": "⏳ <b>C'è una review in attesa da parte tua</b>",
        "nudge_pending_body": "Non hai ancora dato un verdetto.",
        "nudge_overdue": "In ritardo di: {duration} di tempo lavorativo",
        "nudge_open_discussion": "Apri la discussione",
        "author_changes_requested_title": "✍️ <b>Modifiche richieste</b>",
        "author_changes_requested_body": "{reviewer} ha lasciato un commento sulla tua review.",
        "unit_minute": "min",
        "unit_hour": "h",
        "status_header": "<b>Review in attesa da parte tua</b>",
        "status_author_header": "<b>Review in attesa delle tue modifiche</b>",
        "status_line_deadline": "scadenza",
        "status_author_line_suffix": "modifiche richieste da: {who}",
        "nothing_pending": "Non hai nulla in sospeso. 🎉",
        "start_message": (
            "Ciao! Tengo d'occhio le review nel canale e scrivo in privato a chi ha "
            "la palla in mano.\n\n"
            "Ora so come contattarti — se una review resta a te, ti manderò un "
            "promemoria durante l'orario di lavoro.\n\n"
            "Comandi:\n"
            "/status — cosa è in sospeso su di te\n"
            "/announce — preparo il post del canale al posto tuo\n"
            "/link &lt;utente-gitlab&gt; — collega il tuo account GitLab\n"
            "/lang &lt;codice&gt; — cambia la lingua del bot\n"
            "/mute 2h — silenzia\n"
            "/unmute — riattiva i promemoria"
        ),
        "start_found_open": "\n\nHo trovato review aperte su di te: {count}. Guardale — /status",
        "start_found_authored": (
            "\n\nOra ti avviserò delle modifiche sulle tue review ({count}). Guarda — /status"
        ),
        "no_username": (
            "Non hai uno @username impostato su Telegram. Riconosco i revisori dallo "
            "username nel post, quindi senza non posso collegarti a una review — "
            "imposta uno username nelle impostazioni di Telegram e scrivimi di nuovo /start."
        ),
        "link_usage": "Formato: <code>/link ivanov</code>",
        "link_done": "Collegato a GitLab: <code>{login}</code>",
        "mute_usage": "Formato: <code>/mute 2h</code> oppure <code>/mute 1d</code>",
        "mute_done": "Silenzioso per {duration}. Riattiva con /unmute",
        "unmute_done": "Ricomincio a ricordartelo.",
        "lang_usage": "Formato: <code>/lang en</code>. Disponibili: {list}",
        "lang_done": "Lingua del bot: {name}.",
        "registration_hint": (
            "{who} — non posso scrivervi in privato finché non iniziate una "
            "conversazione con me. Aprite @{bot} e premete /start, altrimenti i "
            "promemoria per questa review non vi arriveranno."
        ),
        "not_a_reviewer": "Non sei assegnato come revisore su questa review.",
        "review_gone": "Non trovo questa review — forse è già stata chiusa.",
        "answer_approved": "👍 Approvazione registrata",
        "answer_changes": "✍️ Segnato: richieste modifiche",
        "answer_fixed": "I revisori sono stati avvisati che le modifiche sono pronte",
        "answer_already": "Già {state}",
        "answer_closed_by_approval": "👍 Approvazione registrata, review chiusa",
        "answer_nothing_to_fix": "Nessuno è in attesa di modifiche su questa review al momento",
        "answer_review_closed": "Review chiusa",
        "answer_already_closed": "La review è già chiusa",
        "answer_already_reviewer": "Sei già revisore di questa review",
        "answer_now_reviewer": "Ora sei revisore di questa review",
        "snoozed_hour": "Silenzioso per 1 h",
        "snoozed_tomorrow": "Silenzioso fino a domani",
        "announce_no_username": (
            "Non hai uno @username impostato su Telegram — senza non c'è nessuno da "
            "mettere in \"Autore:\". Imposta uno username e riprova."
        ),
        "announce_no_mr": "Non ho trovato nessun link a un MR in quel messaggio.",
        "announce_project_unconfigured": (
            "Il progetto «{project}» non è configurato per /announce — chiedi "
            "di aggiungerlo a REVIEW_PROJECTS."
        ),
        "announce_conflicting_projects": (
            "Il progetto «{base}» è configurato diversamente da {others} — si possono "
            "unire più progetti in un solo /announce solo se la loro voce in "
            "REVIEW_PROJECTS coincide (prodotto/techlead/pool). Dividi in post separati "
            "o allinea le loro configurazioni."
        ),
        "announce_preview_intro": "Ecco come verrà — controlla e pubblica se va bene.",
        "btn_announce_publish": "✅ Pubblica",
        "btn_announce_reroll": "🔁 Altro revisore",
        "btn_announce_cancel": "❌ Annulla",
        "btn_menu_status": "📋 Stato",
        "btn_menu_announce": "📢 Annuncio",
        "btn_menu_stats": "📊 Statistiche",
        "btn_announce_skip": "⏭ Salta",
        "announce_step_title": "Come si chiama l'attività? Basta una riga.",
        "announce_step_merge_requests": (
            "Link MR/PR — possono essere più di uno, uno per riga. "
            "Premi Salta se non ce ne sono."
        ),
        "announce_step_product": "A quale prodotto appartiene?",
        "announce_step_docs": "Un link alla documentazione? Oppure premi Salta.",
        "announce_step_description": (
            "Allora descrivila in una frase — finirà nel post come riga "
            "«Descrizione:». Oppure premi Salta."
        ),
        "announce_step_task": "Un link all'attività? Oppure premi Salta.",
        "announce_step_no_mr": "Non vedo link MR/PR. Inviali di nuovo o premi Salta.",
        "announce_step_no_url": "Non vedo nessun link. Invialo di nuovo o premi Salta.",
        "announce_nothing_provided": (
            "Non c'è nulla con cui creare il post: né MR, né documentazione, né "
            "attività, né descrizione. Ricomincia con /announce."
        ),
        "announce_no_products": (
            "Nessun progetto è configurato per /announce — chiedi di aggiungerli "
            "a REVIEW_PROJECTS."
        ),
        "announce_draft_gone": (
            "Non trovo questa bozza — forse è già stata pubblicata o annullata."
        ),
        "announce_not_yours": "Questa bozza non è tua.",
        "announce_channel_unconfigured": (
            "Il bot non ha un CHANNEL_ID configurato — nessun posto dove pubblicare."
        ),
        "announce_publish_failed": (
            "Non sono riuscito a pubblicare nel canale — controlla che il bot "
            "sia admin del canale con diritto di postare."
        ),
        "announce_published": "Pubblicato ✅",
        "announce_published_body": "✅ <b>Pubblicato nel canale.</b>",
        "announce_rerolled": "Scelto un altro revisore",
        "announce_cancelled": "Bozza annullata",
        "stats_report_title": "📊 <b>Statistiche review: {since}–{until}</b>",
        "stats_report_empty": "Nessun dato per questo periodo.",
        "stats_fix_time_header": "<b>Più lenti a correggere dopo una richiesta di modifiche:</b>",
        "stats_fix_time_empty": "Nessuna correzione in questo periodo.",
        "stats_response_time_header": "<b>Più lenti a dare un primo verdetto:</b>",
        "stats_response_time_empty": "Nessun primo verdetto in questo periodo.",
        "stats_person_line": "• {label} — mediana {median} ({count})",
        "stats_command_no_access": "Questo comando non è disponibile per te.",
    },
    "zh": {
        "btn_approve": "👍 通过",
        "btn_request_changes": "✍️ 需要修改",
        "btn_fixed": "✅ 已修复",
        "btn_close": "🗄 关闭",
        "btn_snooze_hour": "🔕 1小时",
        "btn_snooze_tomorrow": "🔕 明天再说",
        "btn_open_review": "打开评审",
        "btn_claim": "🙋 我是评审人",
        "state_pending": "⏳ 等待评审",
        "state_changes_requested": "✍️ 等待作者修改",
        "state_awaiting_recheck": "🔁 修改已完成，等待复查",
        "state_approved": "👍 已通过",
        "default_headline": "评审",
        "card_closed": "✅ <b>已关闭</b> — {approvals}/{needed}",
        "card_progress": "通过数：{approvals}/{needed}",
        "card_unparsed_reviewers": "⚠️ 无法从该帖子中识别评审人，请点击下方按钮将评审指派给自己。",
        "nudge_stale_title": "🔁 <b>修改已完成，但 ✍️ 状态未变</b>",
        "nudge_stale_body": "作者已经处理了你提出的全部意见，但你还没有给出通过。",
        "nudge_pending_title": "⏳ <b>有一个评审在等你</b>",
        "nudge_pending_body": "你还没有给出结论。",
        "nudge_overdue": "已超时：{duration}（工作时间）",
        "nudge_open_discussion": "打开讨论",
        "author_changes_requested_title": "✍️ <b>有人要求修改</b>",
        "author_changes_requested_body": "{reviewer} 对你的评审提出了意见。",
        "unit_minute": "分钟",
        "unit_hour": "小时",
        "status_header": "<b>等待你处理的评审</b>",
        "status_author_header": "<b>等待你修改的评审</b>",
        "status_line_deadline": "截止",
        "status_author_line_suffix": "要求修改的人：{who}",
        "nothing_pending": "目前没有等待你处理的评审。🎉",
        "start_message": (
            "你好！我会跟踪频道里的评审，并私信提醒当前轮到谁处理。\n\n"
            "现在我知道怎么联系你了——如果有评审落在你身上，我会在工作时间内提醒你。\n\n"
            "命令：\n"
            "/status — 查看当前等待你处理的评审\n"
            "/announce — 帮你生成频道公告\n"
            "/link &lt;GitLab用户名&gt; — 关联你的 GitLab 账号\n"
            "/lang &lt;语言代码&gt; — 切换机器人语言\n"
            "/mute 2h — 暂停提醒\n"
            "/unmute — 恢复提醒"
        ),
        "start_found_open": "\n\n发现 {count} 个等待你处理的评审，查看 — /status",
        "start_found_authored": "\n\n现在会通知你评审意见（{count} 个）。查看 — /status",
        "no_username": (
            "你在 Telegram 中没有设置 @username。我是通过帖子里的用户名来识别评审人的，"
            "没有用户名就无法把你和评审关联起来——请在 Telegram 设置中添加用户名，"
            "然后再给我发一次 /start。"
        ),
        "link_usage": "格式：<code>/link ivanov</code>",
        "link_done": "已关联 GitLab：<code>{login}</code>",
        "mute_usage": "格式：<code>/mute 2h</code> 或 <code>/mute 1d</code>",
        "mute_done": "将在 {duration} 内保持安静。恢复提醒 — /unmute",
        "unmute_done": "已恢复提醒。",
        "lang_usage": "格式：<code>/lang en</code>。可选：{list}",
        "lang_done": "机器人语言：{name}。",
        "registration_hint": (
            "{who} — 在你们和我开始对话之前，我无法给你们发私信。请打开 @{bot} 并点击 "
            "/start，否则你们收不到这个评审的提醒。"
        ),
        "not_a_reviewer": "你不是这个评审的指定评审人。",
        "review_gone": "找不到这个评审——它可能已经关闭了。",
        "answer_approved": "👍 已记录通过",
        "answer_changes": "✍️ 已标记：需要修改",
        "answer_fixed": "已通知评审人修改已完成",
        "answer_already": "已经是 {state}",
        "answer_closed_by_approval": "👍 已记录通过，评审已关闭",
        "answer_nothing_to_fix": "目前没有人在等待这个评审的修改",
        "answer_review_closed": "评审已关闭",
        "answer_already_closed": "评审已经是关闭状态",
        "answer_already_reviewer": "你已经是这个评审的评审人了",
        "answer_now_reviewer": "你现在是这个评审的评审人了",
        "snoozed_hour": "1小时内不再提醒",
        "snoozed_tomorrow": "明天之前不再提醒",
        "announce_no_username": (
            "你在 Telegram 中没有设置 @username——没有它就无法填写「作者：」。"
            "请在 Telegram 设置中添加用户名后重试。"
        ),
        "announce_no_mr": "没有在消息中找到 MR 链接。",
        "announce_project_unconfigured": (
            "项目「{project}」未在 REVIEW_PROJECTS 中配置 /announce，请找人添加。"
        ),
        "announce_conflicting_projects": (
            "项目「{base}」与 {others} 的配置不一致——只有当涉及的仓库在 "
            "REVIEW_PROJECTS 中配置相同（产品/负责人/候选池）时，才能在同一条 "
            "/announce 里合并。请拆成多条公告，或统一它们的配置。"
        ),
        "announce_preview_intro": "预览如下——确认无误后发布。",
        "btn_announce_publish": "✅ 发布",
        "btn_announce_reroll": "🔁 换一个评审人",
        "btn_announce_cancel": "❌ 取消",
        "btn_menu_status": "📋 状态",
        "btn_menu_announce": "📢 公告",
        "btn_menu_stats": "📊 统计",
        "btn_announce_skip": "⏭ 跳过",
        "announce_step_title": "任务叫什么？一行就够。",
        "announce_step_merge_requests": "MR/PR 链接——可以多个，每行一个。没有就点「跳过」。",
        "announce_step_product": "属于哪个产品？",
        "announce_step_docs": "文档链接？或者点「跳过」。",
        "announce_step_description": (
            "那就用一句话描述一下——会作为「描述：」写进帖子。或者点「跳过」。"
        ),
        "announce_step_task": "任务链接？或者点「跳过」。",
        "announce_step_no_mr": "没看到 MR/PR 链接。请重新发送，或点「跳过」。",
        "announce_step_no_url": "没看到链接。请重新发送，或点「跳过」。",
        "announce_nothing_provided": (
            "没有可用来生成帖子的内容：没有 MR、文档、任务，也没有描述。请用 /announce 重新开始。"
        ),
        "announce_no_products": (
            "没有任何项目配置了 /announce——请让管理员把它们加到 REVIEW_PROJECTS。"
        ),
        "announce_draft_gone": "找不到这个草稿——可能已经发布或取消了。",
        "announce_not_yours": "这不是你的草稿。",
        "announce_channel_unconfigured": "机器人未设置 CHANNEL_ID——没有可发布的频道。",
        "announce_publish_failed": "发布到频道失败——请确认机器人是频道管理员且有发布权限。",
        "announce_published": "已发布 ✅",
        "announce_published_body": "✅ <b>已发布到频道。</b>",
        "announce_rerolled": "已换一个评审人",
        "announce_cancelled": "草稿已取消",
        "stats_report_title": "📊 <b>评审统计：{since}–{until}</b>",
        "stats_report_empty": "此时间段内没有数据。",
        "stats_fix_time_header": "<b>要求修改后修复最慢的人：</b>",
        "stats_fix_time_empty": "此时间段内没有完成的修复。",
        "stats_response_time_header": "<b>给出首次结论最慢的人：</b>",
        "stats_response_time_empty": "此时间段内没有首次结论。",
        "stats_person_line": "• {label} — 中位数 {median}（{count} 次）",
        "stats_command_no_access": "你没有权限使用此命令。",
    },
}

_STATE_KEYS: dict[ReviewerState, str] = {
    ReviewerState.PENDING: "state_pending",
    ReviewerState.CHANGES_REQUESTED: "state_changes_requested",
    ReviewerState.AWAITING_RECHECK: "state_awaiting_recheck",
    ReviewerState.APPROVED: "state_approved",
}


def t(locale: str, key: str, **kwargs: object) -> str:
    """Look up one string. Falls back to English, then to the raw key, so a gap in a
    translation degrades to readable English rather than a crash or a blank line.

    Substituted values are escaped, the template is not: the template is our own copy
    and owns whatever `<b>` it carries, while every kwarg is runtime data (a @handle,
    a GitLab login typed into /link) that must not be able to inject markup.
    """
    table = _STRINGS.get(locale) or _STRINGS[FALLBACK_LOCALE]
    template = table.get(key) or _STRINGS[FALLBACK_LOCALE].get(key, key)
    if not kwargs:
        return template
    return template.format(**{name: esc(value) for name, value in kwargs.items()})


def state_label(locale: str, state: ReviewerState) -> str:
    return t(locale, _STATE_KEYS[state])


def registration_hint(locale: str, labels: list[str], bot_username: str) -> str:
    return t(locale, "registration_hint", who=", ".join(labels), bot=bot_username)


def card(
    locale: str,
    headline: str,
    rows: list[tuple[str, ReviewerState]],
    *,
    is_closed: bool,
    approvals: int,
    required_approvals: int,
    merge_requests: list[tuple[str, str]],
    unparsed_reviewers: bool = False,
) -> str:
    """`merge_requests` are (label, url) pairs, e.g. ("utils!223", "https://...")."""
    lines = [f"<b>{esc(headline)}</b>"]

    if is_closed:
        lines.append(
            "\n" + t(locale, "card_closed", approvals=approvals, needed=required_approvals)
        )
    else:
        lines.append(
            "\n" + t(locale, "card_progress", approvals=approvals, needed=required_approvals)
        )

    if unparsed_reviewers:
        lines.append("\n" + t(locale, "card_unparsed_reviewers"))
    elif rows:
        lines.append("")
        lines.extend(f"• {esc(label)} — {state_label(locale, state)}" for label, state in rows)

    if merge_requests:
        lines.append("")
        lines.extend(f'<a href="{esc(url)}">{esc(label)}</a>' for label, url in merge_requests)

    return "\n".join(lines)


def nudge(
    locale: str,
    reason: NudgeReason,
    headline: str,
    overdue_by: timedelta,
    review_url: str | None,
    merge_request_urls: list[str],
) -> str:
    if reason is NudgeReason.STALE_CHANGES_REQUESTED:
        head = f"{t(locale, 'nudge_stale_title')}\n\n{t(locale, 'nudge_stale_body')}"
    else:
        head = f"{t(locale, 'nudge_pending_title')}\n\n{t(locale, 'nudge_pending_body')}"

    lines = [
        head,
        "",
        f"<b>{esc(headline)}</b>",
        t(locale, "nudge_overdue", duration=humanize(locale, overdue_by)),
    ]
    if merge_request_urls:
        lines.append("")
        lines.extend(esc(url) for url in merge_request_urls)
    if review_url:
        lines.append("")
        lines.append(f'<a href="{esc(review_url)}">{t(locale, "nudge_open_discussion")}</a>')
    return "\n".join(lines)


def status_line(
    locale: str,
    headline: str,
    state: ReviewerState,
    deadline: datetime,
    tz_hours: int,
    url: str | None = None,
) -> str:
    local = deadline.astimezone(_tz(tz_hours))
    word = t(locale, "status_line_deadline")
    title = f'<a href="{esc(url)}">{esc(headline)}</a>' if url else esc(headline)
    return f"• <b>{title}</b> — {state_label(locale, state)} ({word} {local:%d.%m %H:%M})"


def status_author_line(
    locale: str, headline: str, reviewer_labels: list[str], url: str | None = None
) -> str:
    title = f'<a href="{esc(url)}">{esc(headline)}</a>' if url else esc(headline)
    who = ", ".join(reviewer_labels)
    return f"• <b>{title}</b> — {t(locale, 'status_author_line_suffix', who=who)}"


def author_changes_requested(
    locale: str,
    reviewer: str,
    headline: str,
    review_url: str | None,
    merge_request_urls: list[str],
) -> str:
    lines = [
        t(locale, "author_changes_requested_title"),
        "",
        t(locale, "author_changes_requested_body", reviewer=reviewer),
        "",
        f"<b>{esc(headline)}</b>",
    ]
    if merge_request_urls:
        lines.append("")
        lines.extend(esc(url) for url in merge_request_urls)
    if review_url:
        lines.append("")
        lines.append(f'<a href="{esc(review_url)}">{t(locale, "nudge_open_discussion")}</a>')
    return "\n".join(lines)


def humanize(locale: str, delta: timedelta) -> str:
    minute = t(locale, "unit_minute")
    hour = t(locale, "unit_hour")
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} {minute}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} {hour} {minutes} {minute}" if minutes else f"{hours} {hour}"


def _tz(hours: int) -> timezone:
    return timezone(timedelta(hours=hours))
