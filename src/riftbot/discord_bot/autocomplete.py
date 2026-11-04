"""Suggestions d'autocomplétion partagées entre commandes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import discord
from discord import app_commands

from riftbot.application.dto import MatchSummary
from riftbot.application.ladder_service import LadderService
from riftbot.discord_bot.common import guild_id, truncate
from riftbot.discord_bot.views.embeds import match_label
from riftbot.domain.errors import DomainError

MAX_CHOICES = 25  # limite Discord


async def match_choices(
    ladder: LadderService,
    interaction: discord.Interaction[Any],
    current: str,
    keep: Callable[[MatchSummary], bool],
) -> list[app_commands.Choice[int]]:
    """Matchs de la semaine en cours qui satisfont ``keep``, filtrés par le texte saisi."""
    overview = await ladder.week_overview(guild_id(interaction))
    if overview is None or not overview.week.is_open:
        return []
    needle = current.lower()
    choices = [
        app_commands.Choice(name=truncate(match_label(s), 100), value=s.match.pk)
        for s in overview.matches
        if keep(s) and needle in match_label(s).lower()
    ]
    return choices[:MAX_CHOICES]


async def winner_choices(
    ladder: LadderService, interaction: discord.Interaction[Any]
) -> list[app_commands.Choice[int]]:
    """Les deux joueurs du match déjà choisi dans l'option ``match``."""
    match_id = getattr(interaction.namespace, "match", None)
    if not isinstance(match_id, int):
        return []
    try:
        summary = await ladder.get_match(guild_id(interaction), match_id)
    except DomainError:
        return []
    return [
        app_commands.Choice(name=truncate(summary.player1.display_name, 100), value=1),
        app_commands.Choice(name=truncate(summary.player2.display_name, 100), value=2),
    ]
