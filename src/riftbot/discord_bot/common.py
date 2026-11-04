"""Utilitaires partagés par les cogs et les vues."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

import discord
from discord import app_commands

if TYPE_CHECKING:
    from riftbot.bootstrap import Services


class _HasServices(Protocol):
    services: Services


def guild_id(interaction: discord.Interaction[Any]) -> int:
    if interaction.guild_id is None:
        raise app_commands.NoPrivateMessage()
    return interaction.guild_id


def services_of(interaction: discord.Interaction[Any]) -> Services:
    """Services du bot, pour les composants qui n'ont pas de cog (boutons persistants)."""
    return cast(_HasServices, interaction.client).services


async def send_error(interaction: discord.Interaction[Any], message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
