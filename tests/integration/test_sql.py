"""Comportements propres à l'implémentation SQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftbot.domain.economy.models import Wallet
from riftbot.domain.errors import ConcurrentModification
from riftbot.domain.ladder.models import Player
from riftbot.infrastructure.sql.database import (
    MIGRATIONS_DIR,
    create_engine,
    create_session_factory,
    upgrade_database,
)
from riftbot.infrastructure.sql.uow import SqlUnitOfWork


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    await asyncio.to_thread(upgrade_database, url)
    engine = create_engine(url)
    yield create_session_factory(engine)
    await engine.dispose()


def test_migrations_match_the_models(tmp_path: Path) -> None:
    """Échoue si un modèle a changé sans migration : lancer `alembic revision --autogenerate`."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'check.db'}"
    upgrade_database(url)
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    command.check(config)


async def test_datetimes_come_back_in_utc(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registered = datetime(2026, 11, 4, 12, 30, tzinfo=UTC)
    async with SqlUnitOfWork(session_factory) as uow:
        player = await uow.players.add(
            Player(guild_id=1, discord_id=1, display_name="a", rank=1, registered_at=registered)
        )
        await uow.commit()
    async with SqlUnitOfWork(session_factory) as uow:
        loaded = await uow.players.get(player.pk)
    assert loaded is not None
    assert loaded.registered_at == registered
    assert loaded.registered_at.tzinfo is UTC


async def test_concurrent_wallet_updates_are_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with SqlUnitOfWork(session_factory) as uow:
        await uow.wallets.add(Wallet(guild_id=1, user_id=1, balance=100))
        await uow.commit()

    first, second = SqlUnitOfWork(session_factory), SqlUnitOfWork(session_factory)
    async with first, second:
        wallet_a = await first.wallets.get(1, 1)
        wallet_b = await second.wallets.get(1, 1)
        assert wallet_a is not None and wallet_b is not None
        wallet_a.debit(80)
        wallet_b.debit(80)

        await first.wallets.update(wallet_a)
        await first.commit()
        with pytest.raises(ConcurrentModification):
            await second.wallets.update(wallet_b)

    async with SqlUnitOfWork(session_factory) as uow:
        wallet = await uow.wallets.get(1, 1)
    assert wallet is not None and wallet.balance == 20
