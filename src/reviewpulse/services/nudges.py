"""The nudging loop: find whoever is overdue, DM them, write it down.

Delivery is behind a Protocol so the loop can be tested without Telegram.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repo
from ..db.models import ReviewerAssignment, utcnow
from ..domain.escalation import EscalationPolicy, Nudge

logger = logging.getLogger(__name__)


class NudgeSender(Protocol):
    async def send(self, assignment: ReviewerAssignment, nudge: Nudge) -> bool:
        """Deliver one reminder. Return False if the user is unreachable."""
        ...


async def run_nudge_tick(
    session: AsyncSession,
    policy: EscalationPolicy,
    sender: NudgeSender,
    now: datetime | None = None,
) -> list[tuple[ReviewerAssignment, Nudge]]:
    """One pass of the scheduler. Returns what was actually delivered."""
    moment = now or utcnow()

    # Cheap early exit: outside the work window nothing is ever due, and this runs
    # every minute around the clock.
    if not policy.calendar.is_working(moment):
        return []

    delivered: list[tuple[ReviewerAssignment, Nudge]] = []

    for assignment in await repo.nudgeable_assignments(session, moment):
        nudge = policy.evaluate(repo.to_domain(assignment), moment)
        if nudge is None:
            continue

        same_day = policy.nudges_used_today(repo.to_domain(assignment), moment) > 0
        try:
            ok = await sender.send(assignment, nudge)
            error = None
        except Exception as exc:  # a single bad chat must not stall the whole tick
            logger.exception("failed to nudge assignment %s", assignment.id)
            ok, error = False, str(exc)[:500]

        await repo.record_nudge(
            session,
            assignment,
            reason=nudge.reason.value,
            sent_at=moment,
            same_day=same_day,
            delivered=ok,
            error=error,
        )
        if ok:
            delivered.append((assignment, nudge))

    await session.flush()
    return delivered


async def snooze(
    session: AsyncSession, assignment: ReviewerAssignment, until: datetime
) -> None:
    assignment.snoozed_until = until
    await session.flush()
