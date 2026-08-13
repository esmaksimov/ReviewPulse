"""All user-facing copy, in every supported language.

Two audiences need different locales (see `i18n.py`): the shared card and the
registration hint use `Settings.default_locale`, everything else — DMs, command
replies, button-press toasts — uses the individual reviewer's own locale.

Strings live in `_STRINGS[locale][key]`, fetched through `t()`. A handful of composite
messages (the card, a nudge, a status line) are assembled by dedicated functions that
call `t()` for each piece and stitch them together with the runtime data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..domain.escalation import NudgeReason
from ..domain.state import ReviewerState
from ..i18n import FALLBACK_LOCALE

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
        "unit_minute": "мин",
        "unit_hour": "ч",
        "status_header": "<b>Ревью на тебе</b>",
        "status_line_deadline": "дедлайн",
        "nothing_pending": "На тебе ничего не висит. 🎉",
        "start_message": (
            "Привет! Я слежу за ревью в канале и напоминаю, когда мяч на твоей стороне.\n\n"
            "Теперь я знаю, как тебе написать — если на тебе повиснет ревью, пришлю "
            "напоминание в рабочее время.\n\n"
            "Команды:\n"
            "/status — что висит на мне\n"
            "/link &lt;gitlab-логин&gt; — связать с аккаунтом GitLab\n"
            "/lang &lt;код&gt; — сменить язык бота\n"
            "/mute 2h — не беспокоить\n"
            "/unmute — снова беспокоить"
        ),
        "start_found_open": "\n\nНашёл открытых ревью на тебе: {count}. Посмотреть — /status",
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
        "unit_minute": "min",
        "unit_hour": "h",
        "status_header": "<b>Reviews waiting on you</b>",
        "status_line_deadline": "due",
        "nothing_pending": "Nothing is waiting on you. 🎉",
        "start_message": (
            "Hi! I keep an eye on reviews in the channel and DM whoever the ball is on.\n\n"
            "I now know how to reach you — if a review lands on you, I'll send a "
            "reminder during working hours.\n\n"
            "Commands:\n"
            "/status — what's waiting on you\n"
            "/link &lt;gitlab-username&gt; — link your GitLab account\n"
            "/lang &lt;code&gt; — change the bot's language\n"
            "/mute 2h — go quiet\n"
            "/unmute — start reminding again"
        ),
        "start_found_open": "\n\nFound open reviews on you: {count}. Check them — /status",
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
        "unit_minute": "min",
        "unit_hour": "h",
        "status_header": "<b>Revisiones pendientes de ti</b>",
        "status_line_deadline": "plazo",
        "nothing_pending": "No tienes nada pendiente. 🎉",
        "start_message": (
            "¡Hola! Sigo las revisiones del canal y aviso por privado a quien tenga "
            "el turno.\n\n"
            "Ya sé cómo escribirte — si una revisión queda en tu lado, te enviaré un "
            "recordatorio en horario laboral.\n\n"
            "Comandos:\n"
            "/status — qué tienes pendiente\n"
            "/link &lt;usuario-gitlab&gt; — vincular tu cuenta de GitLab\n"
            "/lang &lt;código&gt; — cambiar el idioma del bot\n"
            "/mute 2h — silenciar\n"
            "/unmute — volver a avisar"
        ),
        "start_found_open": "\n\nEncontré revisiones pendientes en ti: {count}. Míralas — /status",
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
        "unit_minute": "min",
        "unit_hour": "h",
        "status_header": "<b>Review in attesa da parte tua</b>",
        "status_line_deadline": "scadenza",
        "nothing_pending": "Non hai nulla in sospeso. 🎉",
        "start_message": (
            "Ciao! Tengo d'occhio le review nel canale e scrivo in privato a chi ha "
            "la palla in mano.\n\n"
            "Ora so come contattarti — se una review resta a te, ti manderò un "
            "promemoria durante l'orario di lavoro.\n\n"
            "Comandi:\n"
            "/status — cosa è in sospeso su di te\n"
            "/link &lt;utente-gitlab&gt; — collega il tuo account GitLab\n"
            "/lang &lt;codice&gt; — cambia la lingua del bot\n"
            "/mute 2h — silenzia\n"
            "/unmute — riattiva i promemoria"
        ),
        "start_found_open": "\n\nHo trovato review aperte su di te: {count}. Guardale — /status",
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
        "unit_minute": "分钟",
        "unit_hour": "小时",
        "status_header": "<b>等待你处理的评审</b>",
        "status_line_deadline": "截止",
        "nothing_pending": "目前没有等待你处理的评审。🎉",
        "start_message": (
            "你好！我会跟踪频道里的评审，并私信提醒当前轮到谁处理。\n\n"
            "现在我知道怎么联系你了——如果有评审落在你身上，我会在工作时间内提醒你。\n\n"
            "命令：\n"
            "/status — 查看当前等待你处理的评审\n"
            "/link &lt;GitLab用户名&gt; — 关联你的 GitLab 账号\n"
            "/lang &lt;语言代码&gt; — 切换机器人语言\n"
            "/mute 2h — 暂停提醒\n"
            "/unmute — 恢复提醒"
        ),
        "start_found_open": "\n\n发现 {count} 个等待你处理的评审，查看 — /status",
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
    translation degrades to readable English rather than a crash or a blank line."""
    table = _STRINGS.get(locale) or _STRINGS[FALLBACK_LOCALE]
    template = table.get(key) or _STRINGS[FALLBACK_LOCALE].get(key, key)
    return template.format(**kwargs) if kwargs else template


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
    lines = [f"<b>{headline}</b>"]

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
        lines.extend(f"• {label} — {state_label(locale, state)}" for label, state in rows)

    if merge_requests:
        lines.append("")
        lines.extend(f'<a href="{url}">{label}</a>' for label, url in merge_requests)

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
        f"<b>{headline}</b>",
        t(locale, "nudge_overdue", duration=humanize(locale, overdue_by)),
    ]
    if merge_request_urls:
        lines.append("")
        lines.extend(merge_request_urls)
    if review_url:
        lines.append("")
        lines.append(f'<a href="{review_url}">{t(locale, "nudge_open_discussion")}</a>')
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
    title = f'<a href="{url}">{headline}</a>' if url else headline
    return f"• <b>{title}</b> — {state_label(locale, state)} ({word} {local:%d.%m %H:%M})"


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
