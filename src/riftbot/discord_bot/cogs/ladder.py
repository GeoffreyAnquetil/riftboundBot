"""Commandes joueurs du ladder : /register, /leaderboard, /matches, /myresult, /profile.

Les cogs sont des contrôleurs fins : ils lisent l'interaction, appellent un service et mettent
en forme la réponse. Les ``DomainError`` sont affichées par le gestionnaire global de ``bot.py``.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from riftbot.bootstrap import Services
from riftbot.discord_bot.common import guild_id, send_error
from riftbot.discord_bot.views import embeds
from riftbot.discord_bot.views.result import ResultModal
from riftbot.domain.errors import NotRegistered

logger = logging.getLogger(__name__)


class LadderCog(commands.Cog):
    def __init__(self, services: Services) -> None:
        self._ladder = services.ladder

    async def cog_load(self) -> None:
        self.auto_confirm.start()

    async def cog_unload(self) -> None:
        self.auto_confirm.cancel()

    @tasks.loop(minutes=10)
    async def auto_confirm(self) -> None:
        try:
            confirmed = await self._ladder.auto_confirm_stale()
        except Exception:
            # Une exception non gérée arrêterait la boucle définitivement.
            logger.exception("Échec de l'auto-confirmation des résultats")
            return
        if confirmed:
            logger.info("%d résultat(s) auto-confirmé(s)", len(confirmed))

    @app_commands.command(name="register", description="Rejoindre le ladder")
    async def register(self, interaction: discord.Interaction[Any]) -> None:
        registration = await self._ladder.register(
            guild_id(interaction), interaction.user.id, interaction.user.display_name
        )
        player = registration.player
        placement = player.rated_matches < self._ladder.placement_matches
        if placement:
            intro = (
                f"Bienvenue dans le ladder, {interaction.user.mention} ! "
                f"Tu commences par **{self._ladder.placement_matches} matchs de placement** "
                f"({player.rated_matches} déjà effectué(s))."
            )
        else:
            intro = (
                f"Bon retour, {interaction.user.mention} ! Tu es **#{player.rank}** "
                f"avec une note de **{player.rating:.0f} ±{player.rating_deviation:.0f}**."
            )
        if registration.match is not None:
            opponent = registration.match.opponent_of(player.discord_id)
            detail = (
                f"\n⚔️ Ton match de la semaine : **{opponent.display_name}** "
                f"(match #{registration.match.match.pk}). Utilise `/myresult` après la partie."
            )
        else:
            detail = (
                "\nTu seras inclus dès qu'un adversaire libre sera disponible cette semaine, "
                "sinon à l'ouverture de la semaine suivante."
            )
        await interaction.response.send_message(intro + detail)

    @app_commands.command(name="leaderboard", description="Classement actuel du ladder")
    async def leaderboard(self, interaction: discord.Interaction[Any]) -> None:
        entries = await self._ladder.leaderboard(guild_id(interaction))
        await interaction.response.send_message(embed=embeds.leaderboard(entries))

    @app_commands.command(name="matches", description="Matchs de la semaine et résultats")
    async def matches(self, interaction: discord.Interaction[Any]) -> None:
        overview = await self._ladder.week_overview(guild_id(interaction))
        if overview is None:
            await send_error(interaction, "Aucune semaine n'a encore été ouverte.")
            return
        await interaction.response.send_message(embed=embeds.week(overview))

    @app_commands.command(name="myresult", description="Déclarer le résultat de ton match")
    # Un délai entre deux ouvertures du formulaire limite les corrections en
    # rafale : chaque soumission crée un message public et de nouveaux boutons.
    @app_commands.checks.cooldown(1, 5.0, key=lambda interaction: interaction.user.id)
    async def myresult(self, interaction: discord.Interaction[Any]) -> None:
        summary = await self._ladder.current_match(guild_id(interaction), interaction.user.id)
        if not summary.match.accepts_reports:
            await send_error(interaction, "Ton match de la semaine n'attend plus de résultat.")
            return
        await interaction.response.send_modal(
            ResultModal(self._ladder, summary, interaction.user.id)
        )

    @app_commands.command(name="profile", description="Statistiques d'un joueur")
    @app_commands.describe(member="Joueur à consulter (toi par défaut)")
    async def profile(
        self, interaction: discord.Interaction[Any], member: discord.Member | None = None
    ) -> None:
        target = member or interaction.user
        gid = guild_id(interaction)
        try:
            data = await self._ladder.profile(gid, target.id)
        except NotRegistered:
            if member is None:
                raise
            await send_error(interaction, f"{target.display_name} ne fait pas partie du ladder.")
            return
        await interaction.response.send_message(embed=embeds.profile(data))
