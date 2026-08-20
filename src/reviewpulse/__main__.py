"""Entry point: start the dispatcher and the scheduler, stop them cleanly."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import Settings, get_settings
from .db.session import Database
from .scheduler.jobs import build_scheduler
from .telegram.bot import ALLOWED_UPDATES, build_bot, build_dispatcher

logger = logging.getLogger(__name__)

def _migrations_root() -> Path:
    """Where alembic.ini and migrations/ live.

    The working directory comes first because that is what the Docker image has
    (the package itself is installed into site-packages, so walking up from
    __file__ would land outside /app). The source-checkout layout is the fallback
    for running the module straight from a clone.
    """
    candidates = (Path.cwd(), Path(__file__).resolve().parents[2])
    for root in candidates:
        if (root / "alembic.ini").is_file() and (root / "migrations").is_dir():
            return root
    raise FileNotFoundError(
        f"alembic.ini + migrations/ not found in any of: {[str(c) for c in candidates]}"
    )


def migrate(settings: Settings) -> None:
    """Bring the schema up to date before anything touches it.

    Runs synchronously, before the event loop starts: Alembic's async template spins
    up its own loop of its own and cannot be called from inside a running one.
    """
    root = _migrations_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


async def run(settings: Settings) -> None:
    database = Database(settings.database_url)

    bot = build_bot(settings)
    dispatcher = build_dispatcher(database, settings)
    scheduler = build_scheduler(bot, database, settings)

    me = await bot.me()
    logger.info(
        "starting as @%s | work %s-%s UTC+%s | SLA %s min | GitLab %s",
        me.username,
        settings.work_start,
        settings.work_end,
        settings.timezone_offset_hours,
        settings.sla_minutes,
        "on" if settings.gitlab_configured else "off",
    )

    scheduler.start()
    try:
        # Drop updates queued while the bot was down: replaying hours-old posts would
        # spray reminders about reviews that are already resolved.
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await database.dispose()


def configure_logging(settings: Settings) -> None:
    """(Re)install the bot's own logging setup, replacing whatever is there.

    Has to be called *after* `migrate()` as well as before it: alembic's env.py runs
    `fileConfig` on alembic.ini, which swaps in its own console handler and pins the
    root logger to WARNING. Those settings would otherwise outlive the migration step
    and silence every INFO the bot emits — reviews tracked, verdicts applied,
    reminders sent — for the rest of the process's life.
    """
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        force=True,
    )


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    migrate(settings)
    configure_logging(settings)
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(run(settings))


if __name__ == "__main__":
    main()
