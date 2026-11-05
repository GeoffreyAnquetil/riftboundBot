"""Déclaration de résultat : la modale /myresult et les boutons Confirmer / Contester.

Les boutons sont des ``DynamicItem`` : le match, le déclarant et le score sont
encodés dans leur ``custom_id``, ils continuent donc de fonctionner après un
redémarrage du bot. L'empreinte du résultat permet de refuser un clic sur un
message devenu obsolète (le déclarant a corrigé son score entre-temps).

Un limiteur très simple protège aussi le bot contre le « clic frénétique » :
sans lui, chaque clic produirait un appel d'API Discord, ce qui peut faire
tomber le bot dans les limites de Discord / Cloudflare.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import discord
from discord import ui

from riftbot.application.dto import MatchSummary, ReportFingerprint, ReportOutcome
from riftbot.application.ladder_service import LadderService
from riftbot.discord_bot.common import guild_id, mention, send_error, services_of, truncate
from riftbot.discord_bot.views.embeds import score
from riftbot.domain.errors import DomainError, InvalidMatchState, NotFound, StaleReport
from riftbot.domain.ladder.models import Match, MatchStatus

logger = logging.getLogger(__name__)

# Un utilisateur ne peut déclencher qu'une action de résultat toutes les deux
# secondes. Les clics suivants sont ignorés volontairement : répondre à chaque
# clic produirait justement l'appel d'API qu'on veut éviter.
BUTTON_COOLDOWN_SECONDS = 2.0
_BUTTON_LAST_USE: dict[int, float] = {}


def _button_is_throttled(user_id: int) -> bool:
    """Indique si ce clic doit être ignoré pour cause de flood.

    Le dictionnaire est local au processus et nettoyé opportunément : il ne
    grossit pas indéfiniment, même sur un serveur très actif.
    """
    now = time.monotonic()
    last_use = _BUTTON_LAST_USE.get(user_id)
    if last_use is not None and now - last_use < BUTTON_COOLDOWN_SECONDS:
        return True

    _BUTTON_LAST_USE[user_id] = now
    if len(_BUTTON_LAST_USE) > 1024:
        cutoff = now - 60.0
        for key in [key for key, value in _BUTTON_LAST_USE.items() if value < cutoff]:
            del _BUTTON_LAST_USE[key]
    return False


async def _retire_message(interaction: discord.Interaction[Any], content: str) -> None:
    """Remplace un message obsolète et retire ses boutons.

    C'est ce qui empêche un vieux message de rester cliquable pour toujours :
    le premier clic sur un message périmé le « consume ».
    """
    try:
        await interaction.response.edit_message(content=content, view=None)
    except discord.HTTPException:
        # Le message a peut-être été supprimé entre-temps : on informe au mieux.
        await send_error(interaction, content)


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
                view=confirmation_view(summary.match),
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
    ui.DynamicItem[ui.Button[ui.View]],
    template=(
        r"result:confirm:"
        r"(?P<match_id>\d+):(?P<reported_by>\d+):(?P<score1>\d+):(?P<score2>\d+)"
    ),
):
    def __init__(self, match_id: int, fingerprint: ReportFingerprint) -> None:
        self.match_id = match_id
        self.fingerprint = fingerprint
        super().__init__(
            ui.Button(
                label="Confirmer",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=(
                    f"result:confirm:{match_id}:{fingerprint.reported_by}:"
                    f"{fingerprint.score1}:{fingerprint.score2}"
                ),
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ConfirmResultButton:
        return cls(
            int(match["match_id"]),
            ReportFingerprint(
                reported_by=int(match["reported_by"]),
                score1=int(match["score1"]),
                score2=int(match["score2"]),
            ),
        )

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        # Flood : on ignore sans répondre, pour ne pas générer un appel d'API
        # par clic. Discord affichera « interaction échouée » côté client.
        if _button_is_throttled(interaction.user.id):
            return

        try:
            summary = await services_of(interaction).ladder.confirm_result(
                guild_id(interaction),
                self.match_id,
                interaction.user.id,
                expected=self.fingerprint,
            )
        except (StaleReport, InvalidMatchState, NotFound):
            # Le message ne correspond plus à l'état courant : on le désactive
            # pour qu'il ne puisse plus être cliqué.
            await _retire_message(
                interaction,
                "⌛ Ce message n'est plus valable : utilise le message le plus récent.",
            )
            return
        except DomainError as error:
            # Erreur récupérable (mauvais utilisateur, conflit concurrent…) :
            # on laisse les boutons en place pour le bon utilisateur.
            await send_error(interaction, str(error))
            return
        except Exception:
            logger.exception("Erreur inattendue dans le bouton Confirmer")
            await send_error(interaction, "Erreur inattendue, réessaie plus tard.")
            return

        await interaction.response.edit_message(content=confirmed_message(summary), view=None)


class DisputeResultButton(
    ui.DynamicItem[ui.Button[ui.View]],
    template=(
        r"result:dispute:"
        r"(?P<match_id>\d+):(?P<reported_by>\d+):(?P<score1>\d+):(?P<score2>\d+)"
    ),
):
    def __init__(self, match_id: int, fingerprint: ReportFingerprint) -> None:
        self.match_id = match_id
        self.fingerprint = fingerprint
        super().__init__(
            ui.Button(
                label="Contester",
                emoji="⚠️",
                style=discord.ButtonStyle.danger,
                custom_id=(
                    f"result:dispute:{match_id}:{fingerprint.reported_by}:"
                    f"{fingerprint.score1}:{fingerprint.score2}"
                ),
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> DisputeResultButton:
        return cls(
            int(match["match_id"]),
            ReportFingerprint(
                reported_by=int(match["reported_by"]),
                score1=int(match["score1"]),
                score2=int(match["score2"]),
            ),
        )

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        if _button_is_throttled(interaction.user.id):
            return

        try:
            summary = await services_of(interaction).ladder.dispute_result(
                guild_id(interaction),
                self.match_id,
                interaction.user.id,
                expected=self.fingerprint,
            )
        except (StaleReport, InvalidMatchState, NotFound):
            await _retire_message(
                interaction,
                "⌛ Ce message n'est plus valable : utilise le message le plus récent.",
            )
            return
        except DomainError as error:
            await send_error(interaction, str(error))
            return
        except Exception:
            logger.exception("Erreur inattendue dans le bouton Contester")
            await send_error(interaction, "Erreur inattendue, réessaie plus tard.")
            return

        await interaction.response.edit_message(content=disputed_message(summary), view=None)


def confirmation_view(match: Match) -> ui.View:
    """Vue des deux boutons, liée à la déclaration courante du match."""
    fingerprint = ReportFingerprint.of(match)
    if fingerprint is None:
        # Sécurité : on ne crée jamais de boutons pour un match sans déclaration.
        raise ValueError("impossible de créer des boutons sans résultat déclaré")

    view = ui.View(timeout=None)
    view.add_item(ConfirmResultButton(match.pk, fingerprint))
    view.add_item(DisputeResultButton(match.pk, fingerprint))
    return view
