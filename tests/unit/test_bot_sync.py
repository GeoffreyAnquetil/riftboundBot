from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from riftbot.bootstrap import Services, build_services
from riftbot.config import Settings
from riftbot.discord_bot.bot import RiftBot
from riftbot.discord_bot.cogs.ladder import LadderCog
from riftbot.infrastructure.memory.uow import InMemoryStore, InMemoryUnitOfWork


def memory_services() -> Services:
    store = InMemoryStore()
    return build_services(Settings(_env_file=None), lambda: InMemoryUnitOfWork(store))


def _cancel_loop(bot: RiftBot) -> None:
    cog = bot.get_cog("LadderCog")
    assert isinstance(cog, LadderCog)
    cog.auto_confirm.cancel()


async def test_unchanged_commands_are_not_synced_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "command_sync.sha256"
    bot = RiftBot(memory_services(), sync_cache=cache)
    await bot.register_components()
    try:
        sync = AsyncMock(return_value=[])
        monkeypatch.setattr(bot.tree, "sync", sync)

        await bot.sync_commands()
        await bot.sync_commands()

        assert sync.await_count == 1
        assert cache.read_text(encoding="utf-8").strip()
    finally:
        _cancel_loop(bot)


async def test_force_sync_bypasses_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = RiftBot(
        memory_services(),
        sync_cache=tmp_path / "command_sync.sha256",
        force_sync=True,
    )
    await bot.register_components()
    try:
        sync = AsyncMock(return_value=[])
        monkeypatch.setattr(bot.tree, "sync", sync)

        await bot.sync_commands()
        await bot.sync_commands()

        assert sync.await_count == 2
    finally:
        _cancel_loop(bot)


async def test_sync_failure_does_not_abort_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "command_sync.sha256"
    bot = RiftBot(memory_services(), sync_cache=cache)
    await bot.register_components()
    try:
        response = SimpleNamespace(status=500, reason="boom")
        error = discord.HTTPException(response, {"message": "boom", "code": 0})
        sync = AsyncMock(side_effect=error)
        monkeypatch.setattr(bot.tree, "sync", sync)

        # Une erreur de synchronisation ne doit pas faire échouer setup_hook,
        # sinon Docker redémarre le bot en boucle.
        await bot.sync_commands()

        assert sync.await_count == 1
        assert not cache.exists()
    finally:
        _cancel_loop(bot)
