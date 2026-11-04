from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from riftbot.application.ports import UnitOfWork, UnitOfWorkFactory
from riftbot.bootstrap import Services, build_services
from riftbot.config import Settings
from riftbot.infrastructure.event_bus import LocalEventBus
from riftbot.infrastructure.memory.uow import InMemoryStore, InMemoryUnitOfWork
from riftbot.infrastructure.sql.database import (
    create_engine,
    create_session_factory,
    upgrade_database,
)
from riftbot.infrastructure.sql.uow import SqlUnitOfWork

GUILD = 1000


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 11, 2, 18, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=["memory", "sqlite"])
async def uow_factory(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[UnitOfWorkFactory]:
    """Chaque test de service tourne sur les deux implémentations : elles doivent se comporter
    pareil. La base SQLite est créée par les migrations Alembic, comme en production."""
    if request.param == "memory":
        store = InMemoryStore()

        def memory_factory() -> UnitOfWork:
            return InMemoryUnitOfWork(store)

        yield memory_factory
        return

    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    await asyncio.to_thread(upgrade_database, url)  # Alembic lance sa propre boucle asyncio
    engine = create_engine(url)
    session_factory = create_session_factory(engine)

    def sql_factory() -> UnitOfWork:
        return SqlUnitOfWork(session_factory)

    yield sql_factory
    await engine.dispose()


@pytest.fixture
def services(uow_factory: UnitOfWorkFactory, clock: FakeClock) -> Services:
    settings = Settings(_env_file=None, best_of=3, auto_confirm_hours=24, starting_balance=1000)
    return build_services(settings, uow_factory, clock, LocalEventBus(raise_errors=True))
