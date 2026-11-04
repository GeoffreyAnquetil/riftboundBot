"""Commandes de paris et d'économie : /bet, /balance, /richest, /mybets."""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from riftbot.bootstrap import Services
from riftbot.discord_bot.autocomplete import match_choices, winner_choices
from riftbot.discord_bot.common import guild_id, is_fake
from riftbot.discord_bot.views import embeds
from riftbot.domain.errors import NotFound
from riftbot.domain.ladder.models import Side


class BettingCog(commands.Cog):
    def __init__(self, services: Services) -> None:
        self._ladder = services.ladder
        self._betting = services.betting

    @app_commands.command(name="bet", description="Parier sur un match de la semaine")
    @app_commands.describe(match="Match à parier", winner="Ton pronostic", amount="Ta mise")
    async def bet(
        self,
        interaction: discord.Interaction[Any],
        match: int,
        winner: int,
        amount: app_commands.Range[int, 1],
    ) -> None:
        if winner not in (Side.ONE, Side.TWO):
            raise NotFound("Choisis le vainqueur dans la liste proposée.")
        side = Side(winner)
        gid = guild_id(interaction)
        bet = await self._betting.place_bet(gid, interaction.user.id, match, side, amount)
        summary = await self._ladder.get_match(gid, match)
        await interaction.response.send_message(
            f"🎲 {interaction.user.mention} mise **{embeds.amount(bet.amount)}** sur "
            f"**{summary.player(side).display_name}** ({embeds.match_label(summary)})."
        )

    @bet.autocomplete("match")
    async def _bet_match(
        self, interaction: discord.Interaction[Any], current: str
    ) -> list[app_commands.Choice[int]]:
        return await match_choices(
            self._ladder, interaction, current, lambda s: s.match.is_open_for_bets
        )

    @bet.autocomplete("winner")
    async def _bet_winner(
        self, interaction: discord.Interaction[Any], current: str
    ) -> list[app_commands.Choice[int]]:
        return await winner_choices(self._ladder, interaction)

    @app_commands.command(name="balance", description="Ton solde")
    async def balance(self, interaction: discord.Interaction[Any]) -> None:
        wallet = await self._betting.balance(guild_id(interaction), interaction.user.id)
        await interaction.response.send_message(
            f"Ton solde : **{embeds.amount(wallet.balance)}**", ephemeral=True
        )

    @app_commands.command(name="richest", description="Classement des plus riches")
    async def richest(self, interaction: discord.Interaction[Any]) -> None:
        gid = guild_id(interaction)
        wallets = await self._betting.richest(gid)
        fake_names = {
            e.player.discord_id: e.player.display_name
            for e in await self._ladder.leaderboard(gid)
            if is_fake(e.player.discord_id)
        }
        await interaction.response.send_message(embed=embeds.richest(wallets, fake_names))

    @app_commands.command(name="mybets", description="Tes derniers paris")
    async def mybets(self, interaction: discord.Interaction[Any]) -> None:
        summaries = await self._betting.user_bets(guild_id(interaction), interaction.user.id)
        await interaction.response.send_message(embed=embeds.bets(summaries), ephemeral=True)
