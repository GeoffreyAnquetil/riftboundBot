"""Déclaration de résultat : la modale /myresult et les boutons Confirmer / Contester.

Les boutons sont des ``DynamicItem`` : l'id du match est encodé dans leur ``custom_id``, ils
continuent donc de fonctionner après un redémarrage du bot.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import ui

from riftbot.application.dto import MatchSummary, ReportOutcome
from riftbot.application.ladder_service import LadderService
from riftbot.discord_bot.common import guild_id, mention, send_error, services_of, truncate
from riftbot.discord_bot.views.embeds import score
from riftbot.domain.errors import DomainError
from riftbot.domain.ladder.models import MatchStatus

logger = logging.getLogger(__name__)


def confirmed_message(summary: MatchSummary) -> str:
    winner = summary.winner
    name = winner.display_name if winner else "?"
    return f"✅ Match #{summary.match.pk} validé : **{name}** gagne {score(summary.match)}."


def disputed_message(summary: MatchSummary) -> str:
    return (
        f"⚠️ Litige sur le match #{summary.match.pk} "
        f"({summary.player1.display_name} vs {summary.player2.display_name}) : "
        "un admin doit trancher avec `/admin set-result`."
    )


async def announce_report(
    interaction: discord.Interaction[Any],
    outcome: ReportOutcome,
    reporter_id: int,
    own: int,
    opponent_score: int,
) -> None:
    """Répond à une déclaration : demande de confirmation, validation ou litige."""
    summary = outcome.summary
    match outcome.status:
        case MatchStatus.REPORTED:
            reporter = (
                summary.player2 if summary.player2.discord_id == reporter_id else summary.player1
            )
            opponent = mention(summary.opponent_of(reporter_id))
            await interaction.response.send_message(
                f"{mention(reporter)} déclare **{own}-{opponent_score}** contre {opponent} "
                f"(match #{summary.match.pk}).\n{opponent}, confirme ou conteste ci-dessous. "
                "Sans réponse, le résultat sera validé automatiquement.",
                view=confirmation_view(summary.match.pk),
            )
        case MatchStatus.CONFIRMED:
            await interaction.response.send_message(confirmed_message(summary))
        case _:
            await interaction.response.send_message(disputed_message(summary))


class ResultModal(ui.Modal):
    def __init__(self, ladder: LadderService, summary: MatchSummary, reporter_id: int) -> None:
        self._ladder = ladder
        self._match_id = summary.match.pk
        opponent = summary.opponent_of(reporter_id)
        super().__init__(
            title=truncate(f"Résultat contre {opponent.display_name}", 45), timeout=600
        )

        scores = ladder.match_format.winning_scores()
        options = [
            discord.SelectOption(label=f"Victoire {won}-{lost}", value=f"{won}-{lost}")
            for won, lost in scores
        ]
        options += [
            discord.SelectOption(label=f"Défaite {lost}-{won}", value=f"{lost}-{won}")
            for won, lost in reversed(scores)
        ]
        self.score: ui.Select[ResultModal] = ui.Select(
            placeholder="Choisis le score", options=options
        )
        self.add_item(
            ui.Label(
                text="Ton résultat",
                description=f"Match #{self._match_id}, score vu de ton côté",
                component=self.score,
            )
        )

    async def on_submit(self, interaction: discord.Interaction[Any]) -> None:
        own, opponent = (int(part) for part in self.score.values[0].split("-"))
        outcome = await self._ladder.report_result(
            guild_id(interaction), self._match_id, interaction.user.id, own, opponent
        )
        await announce_report(interaction, outcome, interaction.user.id, own, opponent)

    # Signature imposée par ui.Modal, elle-même incompatible avec BaseView (typage de discord.py).
    async def on_error(  # type: ignore[override]
        self, interaction: discord.Interaction[Any], error: Exception, /
    ) -> None:
        if isinstance(error, DomainError):
            await send_error(interaction, str(error))
            return
        logger.exception("Erreur dans la modale de résultat", exc_info=error)
        await send_error(interaction, "Erreur inattendue, réessaie plus tard.")


class ConfirmResultButton(
    ui.DynamicItem[ui.Button[ui.View]], template=r"result:confirm:(?P<match_id>\d+)"
):
    def __init__(self, match_id: int) -> None:
        super().__init__(
            ui.Button(
                label="Confirmer",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"result:confirm:{match_id}",
            )
        )
        self.match_id = match_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ConfirmResultButton:
        return cls(int(match["match_id"]))

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        try:
            summary = await services_of(interaction).ladder.confirm_result(
                guild_id(interaction), self.match_id, interaction.user.id
            )
        except DomainError as error:
            await send_error(interaction, str(error))
            return
        await interaction.response.edit_message(content=confirmed_message(summary), view=None)


class DisputeResultButton(
    ui.DynamicItem[ui.Button[ui.View]], template=r"result:dispute:(?P<match_id>\d+)"
):
    def __init__(self, match_id: int) -> None:
        super().__init__(
            ui.Button(
                label="Contester",
                emoji="⚠️",
                style=discord.ButtonStyle.danger,
                custom_id=f"result:dispute:{match_id}",
            )
        )
        self.match_id = match_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> DisputeResultButton:
        return cls(int(match["match_id"]))

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        try:
            summary = await services_of(interaction).ladder.dispute_result(
                guild_id(interaction), self.match_id, interaction.user.id
            )
        except DomainError as error:
            await send_error(interaction, str(error))
            return
        await interaction.response.edit_message(content=disputed_message(summary), view=None)


def confirmation_view(match_id: int) -> ui.View:
    view = ui.View(timeout=None)
    view.add_item(ConfirmResultButton(match_id))
    view.add_item(DisputeResultButton(match_id))
    return view
