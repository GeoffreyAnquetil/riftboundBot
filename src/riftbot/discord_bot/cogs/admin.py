"""Commandes d'administration : /admin week-open, week-close, set-result, remove-player.

Réservées par défaut aux membres ayant la permission « Gérer le serveur ». Les admins du
serveur peuvent ajuster ces droits dans Paramètres > Intégrations.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from riftbot.bootstrap import Services
from riftbot.discord_bot.autocomplete import match_choices, winner_choices
from riftbot.discord_bot.common import guild_id, mention
from riftbot.discord_bot.views import embeds
from riftbot.discord_bot.views.result import confirmed_message
from riftbot.domain.errors import InvalidScore, NotFound
from riftbot.domain.ladder.models import Side


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class AdminCog(commands.GroupCog, group_name="admin", group_description="Gestion du ladder"):
    def __init__(self, services: Services) -> None:
        super().__init__()
        self._ladder = services.ladder

    @app_commands.command(
        name="week-open",
        description="Ouvre une semaine, apparie les joueurs et publie les matchs",
    )
    async def week_open(self, interaction: discord.Interaction[Any]) -> None:
        await interaction.response.defer(thinking=True)
        overview = await self._ladder.open_week(guild_id(interaction))
        players = [p for s in overview.matches for p in (s.player1, s.player2)]
        if overview.bye is not None:
            players.append(overview.bye)
        mentions = " ".join(mention(p) for p in players)
        await interaction.followup.send(
            f"📣 La semaine {overview.week.number} est ouverte ! {mentions}",
            embed=embeds.week(overview),
        )

    @app_commands.command(
        name="week-close", description="Clôture la semaine et met à jour les notes"
    )
    async def week_close(self, interaction: discord.Interaction[Any]) -> None:
        await interaction.response.defer(thinking=True)
        closing = await self._ladder.close_week(guild_id(interaction))
        await interaction.followup.send(embed=embeds.week_closing(closing))

    @app_commands.command(name="set-result", description="Fixe le résultat d'un match (litige…)")
    @app_commands.describe(match="Match à trancher", winner="Vainqueur", score="Score du vainqueur")
    async def set_result(
        self, interaction: discord.Interaction[Any], match: int, winner: int, score: str
    ) -> None:
        if winner not in (Side.ONE, Side.TWO):
            raise NotFound("Choisis le vainqueur dans la liste proposée.")
        try:
            won, lost = (int(part) for part in score.split("-"))
        except ValueError:
            raise InvalidScore(f"Score « {score} » illisible : utilise le format 2-1.") from None
        side = Side(winner)
        score1, score2 = (won, lost) if side is Side.ONE else (lost, won)
        summary = await self._ladder.set_result(guild_id(interaction), match, score1, score2)
        await interaction.response.send_message(f"🔨 {confirmed_message(summary)}")

    @set_result.autocomplete("match")
    async def _set_result_match(
        self, interaction: discord.Interaction[Any], current: str
    ) -> list[app_commands.Choice[int]]:
        return await match_choices(
            self._ladder, interaction, current, lambda s: not s.match.is_final
        )

    @set_result.autocomplete("winner")
    async def _set_result_winner(
        self, interaction: discord.Interaction[Any], current: str
    ) -> list[app_commands.Choice[int]]:
        return await winner_choices(self._ladder, interaction)

    @set_result.autocomplete("score")
    async def _set_result_score(
        self, interaction: discord.Interaction[Any], current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=f"{won}-{lost}", value=f"{won}-{lost}")
            for won, lost in self._ladder.match_format.winning_scores()
        ]

    @app_commands.command(name="remove-player", description="Retire un joueur du ladder")
    @app_commands.describe(member="Joueur à retirer")
    async def remove_player(
        self, interaction: discord.Interaction[Any], member: discord.Member
    ) -> None:
        player = await self._ladder.remove_player(guild_id(interaction), member.id)
        await interaction.response.send_message(
            f"{player.display_name} a été retiré du ladder. "
            "Son match en cours est annulé et son adversaire est réapparié si possible."
        )
