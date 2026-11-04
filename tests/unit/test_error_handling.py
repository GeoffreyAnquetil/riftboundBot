"""Gestion des erreurs Discord, avec de fausses interactions (aucune connexion)."""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from riftbot.bootstrap import build_services
from riftbot.config import Settings
from riftbot.discord_bot.bot import RiftBot
from riftbot.discord_bot.common import UNKNOWN_INTERACTION, send_error
from riftbot.infrastructure.memory.uow import InMemoryStore, InMemoryUnitOfWork


def expired() -> discord.NotFound:
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(
        response,
        {"code": UNKNOWN_INTERACTION, "message": "Unknown interaction"},
    )


def fake_interaction(send: AsyncMock) -> tuple[discord.Interaction[Any], AsyncMock]:
    response = MagicMock()
    response.is_done.return_value = False
    response.send_message = send
    interaction = SimpleNamespace(
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        command=SimpleNamespace(qualified_name="mybets"),
        created_at=discord.utils.utcnow() - timedelta(seconds=4),
    )
    return cast(discord.Interaction[Any], interaction), send


async def test_send_error_never_raises() -> None:
    interaction, send = fake_interaction(AsyncMock(side_effect=expired()))
    await send_error(interaction, "boom")
    send.assert_awaited_once()


async def test_expired_interaction_is_logged_without_replying(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = InMemoryStore()
    bot = RiftBot(build_services(Settings(_env_file=None), lambda: InMemoryUnitOfWork(store)))
    interaction, send = fake_interaction(AsyncMock())
    command = cast(app_commands.Command[Any, ..., Any], SimpleNamespace(name="mybets"))

    with caplog.at_level(logging.WARNING):
        await bot._on_app_command_error(
            interaction, app_commands.CommandInvokeError(command, expired())
        )

    send.assert_not_awaited()
    assert "interaction expirée, 4." in caplog.text
