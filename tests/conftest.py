from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from reviewpulse.config import Settings
from reviewpulse.db.session import Database

MSK = timezone(timedelta(hours=3))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        BOT_TOKEN="test:token",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        GITLAB_ENABLED=False,
    )


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.create_all()
    yield db
    await db.dispose()


@pytest_asyncio.fixture
async def session(database: Database):
    """A single long-lived session: in-memory SQLite loses the schema per connection."""
    async with database.session_factory() as session:
        yield session


def msk(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment in July 2026. The 27th is a Monday."""
    return datetime(2026, 7, day, hour, minute, tzinfo=MSK)
