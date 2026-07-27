"""All user-facing copy, in one place."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..domain.escalation import NudgeReason
from ..domain.state import ReviewerState

STATE_LABEL: dict[ReviewerState, str] = {
    ReviewerState.PENDING: "⏳ ждёт ревью",
    ReviewerState.CHANGES_REQUESTED: "✍️ ждёт правок от автора",
    ReviewerState.AWAITING_RECHECK: "🔁 правки готовы, ждёт повторного взгляда",
    ReviewerState.APPROVED: "👍 апрув",
}

BTN_APPROVE = "👍 Апрув"
BTN_REQUEST_CHANGES = "✍️ Поправить"
BTN_FIXED = "✅ Поправил"
BTN_CLOSE = "🗄 Закрыть"
BTN_SNOOZE_HOUR = "🔕 Час"
BTN_SNOOZE_TOMORROW = "🔕 До завтра"
BTN_OPEN_REVIEW = "Открыть ревью"

START = (
    "Привет! Я слежу за ревью в канале и напоминаю, когда мяч на твоей стороне.\n\n"
    "Теперь я знаю, как тебе написать — если на тебе повиснет ревью, пришлю напоминание "
    "в рабочее время.\n\n"
    "Команды:\n"
    "/status — что висит на мне\n"
    "/link &lt;gitlab-логин&gt; — связать с аккаунтом GitLab\n"
    "/mute 2h — не беспокоить\n"
    "/unmute — снова беспокоить"
)

NO_USERNAME = (
    "У тебя не задан @username в Telegram. Я узнаю ревьюверов по нику из поста, "
    "так что без него не смогу связать тебя с ревью — задай ник в настройках Telegram "
    "и напиши мне /start ещё раз."
)

NOT_A_REVIEWER = "Ты не назначен ревьювером на это ревью."
REVIEW_GONE = "Не нахожу это ревью — возможно, оно уже закрыто."
NOTHING_PENDING = "На тебе ничего не висит. 🎉"


def registration_hint(labels: list[str], bot_username: str) -> str:
    who = ", ".join(labels)
    return (
        f"{who} — я не могу писать вам в личку, пока вы не начнёте со мной диалог. "
        f"Откройте @{bot_username} и нажмите /start, иначе напоминания по этому ревью "
        f"до вас не дойдут."
    )


def card(
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
        lines.append(f"\n✅ <b>Закрыто</b> — {approvals} апрува")
    else:
        lines.append(f"\nАпрувов: {approvals}/{required_approvals}")

    if unparsed_reviewers:
        lines.append(
            "\n⚠️ Не смог определить ревьюверов из поста. "
            "Нажмите кнопку ниже, чтобы взять ревью на себя."
        )
    elif rows:
        lines.append("")
        lines.extend(f"• {label} — {STATE_LABEL[state]}" for label, state in rows)

    if merge_requests:
        lines.append("")
        lines.extend(f'<a href="{url}">{label}</a>' for label, url in merge_requests)

    return "\n".join(lines)


def nudge(
    reason: NudgeReason,
    headline: str,
    overdue_by: timedelta,
    review_url: str | None,
    merge_request_urls: list[str],
) -> str:
    if reason is NudgeReason.STALE_CHANGES_REQUESTED:
        head = (
            "🔁 <b>Замечания закрыты, а ✍️ висит</b>\n\n"
            "Автор поправил всё, что ты просил, но апрува от тебя пока нет."
        )
    else:
        head = "⏳ <b>На тебе висит ревью</b>\n\nТы ещё не поставил вердикт."

    lines = [head, "", f"<b>{headline}</b>", f"Просрочка: {humanize(overdue_by)} рабочего времени"]
    if merge_request_urls:
        lines.append("")
        lines.extend(merge_request_urls)
    if review_url:
        lines.append("")
        lines.append(f'<a href="{review_url}">Открыть обсуждение</a>')
    return "\n".join(lines)


def status_line(headline: str, state: ReviewerState, deadline: datetime, tz_hours: int) -> str:
    local = deadline.astimezone(_tz(tz_hours))
    return f"• <b>{headline}</b> — {STATE_LABEL[state]} (дедлайн {local:%d.%m %H:%M})"


def humanize(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"


def _tz(hours: int):
    from datetime import timezone

    return timezone(timedelta(hours=hours))
