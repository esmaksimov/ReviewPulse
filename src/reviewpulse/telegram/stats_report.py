"""Render a `services.stats.StatsReport` into text — the periodic digest and /stats
alike share this, so they can never drift apart.

Sibling to `card.py`/`announcement.py`: Telegram/HTML-aware, kept out of `services/`.
"""

from __future__ import annotations

from datetime import timedelta, timezone

from ..services.stats import PersonStat, StatsReport
from . import texts


def render(report: StatsReport, locale: str, tz_hours: int) -> str:
    tz = timezone(timedelta(hours=tz_hours))
    since = report.since.astimezone(tz)
    until = report.until.astimezone(tz)

    lines = [
        texts.t(locale, "stats_report_title", since=f"{since:%d.%m}", until=f"{until:%d.%m}")
    ]

    if report.is_empty:
        lines.append("")
        lines.append(texts.t(locale, "stats_report_empty"))
        return "\n".join(lines)

    lines.append("")
    lines.append(texts.t(locale, "stats_fix_time_header"))
    lines.extend(_lines(report.author_fix_time, locale, empty_key="stats_fix_time_empty"))

    lines.append("")
    lines.append(texts.t(locale, "stats_response_time_header"))
    lines.extend(
        _lines(report.reviewer_response_time, locale, empty_key="stats_response_time_empty")
    )

    return "\n".join(lines)


def _lines(stats: list[PersonStat], locale: str, *, empty_key: str) -> list[str]:
    if not stats:
        return [texts.t(locale, empty_key)]
    return [
        texts.t(
            locale,
            "stats_person_line",
            label=stat.label,
            median=texts.humanize(locale, stat.median),
            count=stat.sample_count,
        )
        for stat in stats
    ]
