"""Commandes d'administration : /admin week-open, week-close, set-result, correct-result,
remove-player.

Réservées par défaut aux membres ayant la permission « Gérer le serveur ». Les admins du
serveur peuvent ajuster ces droits dans Paramètres > Intégrations.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from riftbot.bootstrap import Services
from riftbot.discord_bot.autocomplete import MAX_CHOICES, match_choices, winner_choices
from riftbot.discord_bot.common import guild_id, mention, send_error
from riftbot.discord_bot.views import embeds
from riftbot.discord_bot.views.correction import correction_view
from riftbot.discord_bot.views.result import confirmed_message
from riftbot.domain.errors import InvalidScore, NotFound
from riftbot.domain.ladder.models import MatchStatus, Side


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class AdminCog(commands.GroupCog, group_name="admin", group_description="Gestion du ladder"):
    def __init__(
        self,
        services: Services,
        *,
        admin_user_ids: frozenset[int] = frozenset(),
    ) -> None:
        super().__init__()
        self._ladder = services.ladder
        self._admin_user_ids = admin_user_ids
        if admin_user_ids:
            # La liste blanche remplace la permission Discord : les commandes restent
            # visibles pour que ces utilisateurs puissent les lancer même sans
            # « Gérer le serveur ». L'accès est vérifié à chaque interaction.
            group = self.__cog_app_commands_group__
            if group is not None:
                group.default_permissions = None

    async def interaction_check(self, interaction: discord.Interaction[Any]) -> bool:
        if self._admin_user_ids:
            return interaction.user.id in self._admin_user_ids
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions is not None and permissions.manage_guild)

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

    @app_commands.command(
        name="set-result",
        description="Fixe ou corrige un résultat (litige, forfait, après clôture)",
    )
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
        # Les matchs validés restent proposés : c'est ainsi qu'un admin corrige un
        # score après la clôture (le service refuse un changement de vainqueur).
        return await match_choices(
            self._ladder,
            interaction,
            current,
            lambda s: s.match.status is not MatchStatus.VOIDED,
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

    @app_commands.command(
        name="correct-result",
        description="Corriger le résultat d'un match de la semaine (liste déroulante)",
    )
    async def correct_result(self, interaction: discord.Interaction[Any]) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        overview = await self._ladder.week_overview(guild_id(interaction))
        if overview is None or not overview.week.is_open:
            await send_error(interaction, "Aucune semaine en cours : ouvre une semaine d'abord.")
            return
        summaries = [s for s in overview.matches if s.match.status is not MatchStatus.VOIDED]
        if not summaries:
            await send_error(interaction, "Aucun match à corriger cette semaine.")
            return
        note = (
            "" if len(summaries) <= MAX_CHOICES else f"\n({MAX_CHOICES} premiers matchs affichés)"
        )
        await interaction.followup.send(
            f"Quel match de la semaine {overview.week.number} veux-tu corriger ?{note}",
            view=correction_view(self._ladder, summaries[:MAX_CHOICES]),
            ephemeral=True,
        )

    @app_commands.command(
        name="refresh-ranks",
        description="Recalcule les places (après un changement de matchs de placement)",
    )
    async def refresh_ranks(self, interaction: discord.Interaction[Any]) -> None:
        await interaction.response.defer(thinking=True)
        changed = await self._ladder.refresh_ranks(guild_id(interaction))
        await interaction.followup.send(
            f"🔄 Classement recalculé : {changed} place(s) mise(s) à jour."
        )

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
