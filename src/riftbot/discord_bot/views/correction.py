"""Correction d'un résultat par un admin, pendant la semaine en cours.

Deux listes déroulantes : le match, puis le nouveau résultat. À la validation, le bot
publie un message public qui mentionne les deux joueurs, pour qu'ils voient la décision.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import discord
from discord import ui

from riftbot.application.dto import MatchSummary
from riftbot.application.ladder_service import LadderService
from riftbot.discord_bot.autocomplete import MAX_CHOICES
from riftbot.discord_bot.common import guild_id, mention, send_error, truncate
from riftbot.discord_bot.views.embeds import match_label, score
from riftbot.domain.errors import DomainError
from riftbot.domain.ladder.models import MatchStatus, Side

logger = logging.getLogger(__name__)

VIEW_TIMEOUT = 300


def _match_label(summary: MatchSummary) -> str:
    """Libellé du match, avec son état, pour la liste déroulante."""
    label = match_label(summary)
    match summary.match.status:
        case MatchStatus.CONFIRMED:
            label = f"{label} — {score(summary.match)}"
        case MatchStatus.REPORTED:
            label = f"{label} — en attente"
        case MatchStatus.DISPUTED:
            label = f"{label} — litige"
    return truncate(label, 100)


def correction_view(ladder: LadderService, summaries: Sequence[MatchSummary]) -> ui.View:
    """Vue de départ : choix du match à corriger."""
    view = ui.View(timeout=VIEW_TIMEOUT)
    view.add_item(MatchSelect(ladder, summaries))
    return view


def outcome_view(ladder: LadderService, summary: MatchSummary) -> ui.View:
    """Vue de confirmation : choix du nouveau résultat."""
    view = ui.View(timeout=VIEW_TIMEOUT)
    view.add_item(OutcomeSelect(ladder, summary))
    return view


class MatchSelect(ui.Select[ui.View]):
    def __init__(self, ladder: LadderService, summaries: Sequence[MatchSummary]) -> None:
        self._ladder = ladder
        self._summaries = {summary.match.pk: summary for summary in summaries}
        super().__init__(
            placeholder="Choisis le match à corriger",
            options=[
                discord.SelectOption(label=_match_label(summary), value=str(summary.match.pk))
                for summary in summaries[:MAX_CHOICES]
            ],
        )

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        summary = self._summaries[int(self.values[0])]
        await interaction.response.edit_message(
            content=(
                f"Match {match_label(summary)} : choisis le nouveau résultat.\n"
                "Les deux joueurs seront prévenus."
            ),
            view=outcome_view(self._ladder, summary),
        )


class OutcomeSelect(ui.Select[ui.View]):
    def __init__(self, ladder: LadderService, summary: MatchSummary) -> None:
        self._ladder = ladder
        self._summary = summary
        options: list[discord.SelectOption] = []
        for side in Side:
            player = summary.player(side)
            for won, lost in ladder.match_format.winning_scores():
                score1, score2 = (won, lost) if side is Side.ONE else (lost, won)
                options.append(
                    discord.SelectOption(
                        label=truncate(f"{player.display_name} gagne {won}-{lost}", 100),
                        value=f"{score1}:{score2}",
                    )
                )
        super().__init__(placeholder="Choisis le nouveau résultat", options=options)

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        score1, score2 = (int(part) for part in self.values[0].split(":"))
        try:
            summary = await self._ladder.set_result(
                guild_id(interaction), self._summary.match.pk, score1, score2
            )
        except DomainError as error:
            await send_error(interaction, str(error))
            return
        except Exception:
            logger.exception("Erreur pendant la correction d'un résultat")
            await send_error(interaction, "Erreur inattendue, réessaie plus tard.")
            return

        await interaction.response.edit_message(
            content=f"✅ Match #{summary.match.pk} corrigé. Les deux joueurs ont été prévenus.",
            view=None,
        )
        await interaction.followup.send(
            corrected_announcement(summary),
            allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=False),
            ephemeral=False,
        )


def corrected_announcement(summary: MatchSummary) -> str:
    """Message public annonçant la correction, avec une mention de chaque joueur."""
    winner = summary.winner
    if winner is None:
        return f"⚠️ Résultat du match #{summary.match.pk} mis à jour par un admin."
    loser = summary.opponent_of(winner.discord_id)
    return (
        f"⚠️ **Résultat corrigé par un admin** (match #{summary.match.pk}) : "
        f"{mention(winner)} gagne {score(summary.match)} contre {mention(loser)}."
    )
