"""Commandes de test : de faux joueurs pour dérouler une semaine complète seul.

Activées uniquement avec ``ENABLE_DEV_COMMANDS=true``. Les faux joueurs sont de vrais joueurs du
ladder, avec un identifiant Discord fictif (inférieur à ``FAKE_ID_LIMIT``). Ces commandes
permettent d'agir à leur place : déclarer, confirmer, contester, parier.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from riftbot.bootstrap import Services
from riftbot.discord_bot.autocomplete import MAX_CHOICES, match_choices, winner_choices
from riftbot.discord_bot.common import FAKE_ID_LIMIT, guild_id, is_fake
from riftbot.discord_bot.views import embeds
from riftbot.discord_bot.views.result import announce_report, confirmed_message, disputed_message
from riftbot.domain.errors import InvalidScore, NotAllowed, NotFound
from riftbot.domain.ladder.models import Side

FAKE_NAMES = (
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliett",
    "Kilo", "Lima", "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo", "Sierra", "Tango",
)  # fmt: skip


def next_fake_players(existing_ids: Iterable[int], count: int) -> list[tuple[int, str]]:
    """Identifiants et noms des ``count`` prochains faux joueurs, à la suite des existants."""
    start = max((i for i in existing_ids if is_fake(i)), default=0) + 1
    players = []
    for discord_id in range(start, start + count):
        if discord_id >= FAKE_ID_LIMIT:
            raise NotAllowed("Trop de faux joueurs.")
        name = FAKE_NAMES[(discord_id - 1) % len(FAKE_NAMES)]
        cycle = (discord_id - 1) // len(FAKE_NAMES)
        players.append((discord_id, f"🤖 {name}" + (f" {cycle + 1}" if cycle else "")))
    return players


def parse_score(score: str) -> tuple[int, int]:
    try:
        own, opponent = (int(part) for part in score.split("-"))
    except ValueError:
        raise InvalidScore(f"Score « {score} » illisible : utilise le format 2-1.") from None
    return own, opponent


def require_fake(discord_id: int) -> int:
    if not is_fake(discord_id):
        raise NotAllowed("Les commandes /dev n'agissent qu'à la place de faux joueurs.")
    return discord_id


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class DevCog(commands.GroupCog, group_name="dev", group_description="Outils de test"):
    def __init__(self, services: Services) -> None:
        super().__init__()
        self._ladder = services.ladder
        self._betting = services.betting

    @app_commands.command(name="seed", description="Ajoute de faux joueurs en bas du ladder")
    @app_commands.describe(count="Nombre de faux joueurs à créer")
    async def seed(
        self, interaction: discord.Interaction[Any], count: app_commands.Range[int, 1, 20]
    ) -> None:
        gid = guild_id(interaction)
        existing = [e.player.discord_id for e in await self._ladder.leaderboard(gid)]
        created = [
            await self._ladder.register(gid, discord_id, name)
            for discord_id, name in next_fake_players(existing, count)
        ]
        listing = ", ".join(f"{p.display_name} (#{p.rank})" for p in created)
        await interaction.response.send_message(
            f"🤖 {len(created)} faux joueurs ajoutés : {listing}"
        )

    @app_commands.command(name="report-as", description="Déclare un résultat pour un faux joueur")
    @app_commands.describe(player="Faux joueur", score="Score vu du faux joueur")
    async def report_as(
        self, interaction: discord.Interaction[Any], player: int, score: str
    ) -> None:
        gid = guild_id(interaction)
        own, opponent = parse_score(score)
        summary = await self._ladder.current_match(gid, require_fake(player))
        outcome = await self._ladder.report_result(gid, summary.match.pk, player, own, opponent)
        await announce_report(interaction, outcome, player, own, opponent)

    @app_commands.command(name="answer-as", description="Confirme/conteste pour un faux joueur")
    @app_commands.describe(player="Faux joueur", decision="Réponse au résultat déclaré")
    @app_commands.choices(
        decision=[
            app_commands.Choice(name="Confirmer", value="confirm"),
            app_commands.Choice(name="Contester", value="dispute"),
        ]
    )
    async def answer_as(
        self,
        interaction: discord.Interaction[Any],
        player: int,
        decision: app_commands.Choice[str],
    ) -> None:
        gid = guild_id(interaction)
        match_id = (await self._ladder.current_match(gid, require_fake(player))).match.pk
        if decision.value == "confirm":
            summary = await self._ladder.confirm_result(gid, match_id, player)
            await interaction.response.send_message(confirmed_message(summary))
        else:
            summary = await self._ladder.dispute_result(gid, match_id, player)
            await interaction.response.send_message(disputed_message(summary))

    @app_commands.command(name="bet-as", description="Parie pour un faux joueur")
    @app_commands.describe(
        player="Faux joueur qui parie", match="Match", winner="Pronostic", amount="Mise"
    )
    async def bet_as(
        self,
        interaction: discord.Interaction[Any],
        player: int,
        match: int,
        winner: int,
        amount: app_commands.Range[int, 1],
    ) -> None:
        if winner not in (Side.ONE, Side.TWO):
            raise NotFound("Choisis le vainqueur dans la liste proposée.")
        gid = guild_id(interaction)
        side = Side(winner)
        bet = await self._betting.place_bet(gid, require_fake(player), match, side, amount)
        summary = await self._ladder.get_match(gid, match)
        name = await self._fake_name(gid, player)
        await interaction.response.send_message(
            f"🎲 **{name}** mise **{embeds.amount(bet.amount)}** sur "
            f"**{summary.player(side).display_name}** ({embeds.match_label(summary)})."
        )

    # --- Autocomplétion ------------------------------------------------------------------------

    @report_as.autocomplete("player")
    @answer_as.autocomplete("player")
    @bet_as.autocomplete("player")
    async def _fake_player(
        self, interaction: discord.Interaction[Any], current: str
    ) -> list[app_commands.Choice[int]]:
        needle = current.lower()
        return [
            app_commands.Choice(
                name=f"#{e.player.rank} {e.player.display_name}", value=e.player.discord_id
            )
            for e in await self._ladder.leaderboard(guild_id(interaction))
            if is_fake(e.player.discord_id) and needle in e.player.display_name.lower()
        ][:MAX_CHOICES]

    @report_as.autocomplete("score")
    async def _score(
        self, interaction: discord.Interaction[Any], current: str
    ) -> list[app_commands.Choice[str]]:
        scores = self._ladder.match_format.winning_scores()
        wins = [(f"Victoire {won}-{lost}", f"{won}-{lost}") for won, lost in scores]
        losses = [(f"Défaite {lost}-{won}", f"{lost}-{won}") for won, lost in reversed(scores)]
        return [app_commands.Choice(name=label, value=value) for label, value in wins + losses]

    @bet_as.autocomplete("match")
    async def _bet_match(
        self, interaction: discord.Interaction[Any], current: str
    ) -> list[app_commands.Choice[int]]:
        return await match_choices(
            self._ladder, interaction, current, lambda s: s.match.is_open_for_bets
        )

    @bet_as.autocomplete("winner")
    async def _bet_winner(
        self, interaction: discord.Interaction[Any], current: str
    ) -> list[app_commands.Choice[int]]:
        return await winner_choices(self._ladder, interaction)

    async def _fake_name(self, guild: int, discord_id: int) -> str:
        for entry in await self._ladder.leaderboard(guild):
            if entry.player.discord_id == discord_id:
                return entry.player.display_name
        return f"Faux joueur {discord_id}"
