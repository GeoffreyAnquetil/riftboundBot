"""Mise en forme des réponses (embeds Discord). Aucune logique métier ici."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import discord

from riftbot.application.dto import (
    BetSummary,
    LeaderboardEntry,
    MatchSummary,
    PlayerProfile,
    Pool,
    WeekClosing,
    WeekOverview,
)
from riftbot.application.simulation import SimulationReport
from riftbot.domain.economy.models import BetStatus, Wallet
from riftbot.domain.economy.payout import odds
from riftbot.domain.ladder.models import Match, MatchStatus, Side

CURRENCY = "💰"
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
DESCRIPTION_LIMIT = 4000
LADDER_COLOR = discord.Color.gold()
ECONOMY_COLOR = discord.Color.green()


def amount(value: int) -> str:
    return f"{value:,}".replace(",", " ") + f" {CURRENCY}"


def rank(position: int) -> str:
    return MEDALS.get(position, f"`#{position}`")


def rating(value: float, deviation: float) -> str:
    return f"{value:.0f} ±{deviation:.0f}"


def score(match: Match) -> str:
    """Score vu du vainqueur, ex. « 2-1 »."""
    if match.score1 is None or match.score2 is None:
        return "?"
    return f"{max(match.score1, match.score2)}-{min(match.score1, match.score2)}"


def match_label(summary: MatchSummary) -> str:
    return f"#{summary.match.pk} {summary.player1.display_name} vs {summary.player2.display_name}"


def _lines(lines: Sequence[str], empty: str) -> str:
    """Joint les lignes en respectant la limite de taille d'une description d'embed."""
    if not lines:
        return empty
    out: list[str] = []
    size = 0
    for index, line in enumerate(lines):
        if size + len(line) + 1 > DESCRIPTION_LIMIT:
            out.append(f"… et {len(lines) - index} de plus")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _entry_line(entry: LeaderboardEntry) -> str:
    status = " · 💤 inactif" if entry.inactive else ""
    return (
        f"{rank(entry.player.rank)} **{entry.player.display_name}** · "
        f"`{rating(entry.rating, entry.deviation)}` · {entry.wins}V {entry.losses}D{status}"
    )


def _placement_line(entry: LeaderboardEntry) -> str:
    return (
        f"🎯 **{entry.player.display_name}** · "
        f"`{rating(entry.rating, entry.deviation)}` · "
        f"placement {entry.rated_matches}/{entry.placement_matches}"
    )


# --- Ladder -------------------------------------------------------------------------------------


def leaderboard(entries: Sequence[LeaderboardEntry]) -> discord.Embed:
    ranked = [e for e in entries if not e.provisional]
    provisional = [e for e in entries if e.provisional]
    embed = discord.Embed(
        title="🏆 Classement du ladder",
        description=_lines(
            [_entry_line(e) for e in ranked],
            "Aucun joueur classé pour l'instant : le classement commence après "
            "les matchs de placement.",
        ),
        color=LADDER_COLOR,
    )
    if provisional:
        embed.add_field(
            name="🎯 En placement",
            value=_lines([_placement_line(e) for e in provisional], "—"),
            inline=False,
        )
    return embed


def _match_status(summary: MatchSummary) -> str:
    match = summary.match
    match match.status:
        case MatchStatus.PENDING:
            return "à jouer"
        case MatchStatus.REPORTED:
            return f"{score(match)} déclaré, en attente de confirmation"
        case MatchStatus.CONFIRMED:
            winner = summary.winner
            return f"🏅 **{winner.display_name if winner else '?'}** gagne {score(match)}"
        case MatchStatus.DISPUTED:
            return "⚠️ litige, en attente d'un admin"
        case MatchStatus.VOIDED:
            return "annulé"


def week(overview: WeekOverview) -> discord.Embed:
    state = "en cours" if overview.week.is_open else "clôturée"
    lines = [
        f"`#{s.match.pk}` **{s.player1.display_name}** vs **{s.player2.display_name}**"
        f" · `{rating(s.player1.rating, s.player1.rating_deviation)}` / "
        f"`{rating(s.player2.rating, s.player2.rating_deviation)}` — " + _match_status(s)
        for s in overview.matches
    ]
    if overview.bye is not None:
        lines.append(f"🎫 **{overview.bye.display_name}** est exempt cette semaine.")
    embed = discord.Embed(
        title=f"⚔️ Semaine {overview.week.number} · {state}",
        description=_lines(lines, "Aucun match cette semaine."),
        color=LADDER_COLOR,
    )
    if overview.warnings:
        embed.add_field(name="Appariement", value="\n".join(overview.warnings), inline=False)
    if overview.week.is_open:
        embed.set_footer(text="Déclare ton résultat avec /myresult")
    return embed


def week_closing(closing: WeekClosing) -> discord.Embed:
    def movement(delta: int) -> str:
        if delta > 0:
            return f"▲{delta}"
        if delta < 0:
            return f"▼{-delta}"
        return "="

    lines = [
        f"{'🎯' if c.provisional else rank(c.new_rank)} **{c.player.display_name}** · "
        f"`{c.new_rating:.0f}` ({c.rating_delta:+.0f}) {movement(c.delta)}"
        for c in closing.ranking
    ]
    embed = discord.Embed(
        title=f"📊 Semaine {closing.week.number} clôturée",
        description=_lines(lines, "Classement vide."),
        color=LADDER_COLOR,
    )
    embed.set_footer(
        text=f"{closing.confirmed} résultat(s) validé(s) automatiquement · "
        f"{closing.voided} match(s) non joué(s) annulé(s)"
    )
    return embed


def profile(data: PlayerProfile) -> discord.Embed:
    played = data.wins + data.losses
    win_rate = f"{100 * data.wins / played:.0f} %" if played else "—"
    if data.provisional:
        status = f"🎯 Placement {data.rated_matches}/{data.placement_matches}"
    elif data.inactive:
        status = f"💤 Inactif ({data.player.inactive_weeks} semaines)"
    else:
        status = "✅ Classé"
    embed = discord.Embed(title=f"👤 {data.player.display_name}", color=LADDER_COLOR)
    embed.add_field(name="Rang", value="🎯" if data.provisional else rank(data.player.rank))
    embed.add_field(name="Meilleur rang", value=rank(data.best_rank))
    embed.add_field(
        name="Classement",
        value=f"`{rating(data.rating, data.deviation)}`",
    )
    embed.add_field(name="Bilan", value=f"{data.wins}V {data.losses}D ({win_rate})")
    embed.add_field(name="Statut", value=status)
    if data.recent:
        recent = []
        for s in data.recent:
            won = s.winner is not None and s.winner.pk == data.player.pk
            opponent = s.opponent_of(data.player.discord_id)
            recent.append(
                f"{'✅' if won else '❌'} {score(s.match)} contre {opponent.display_name}"
            )
        embed.add_field(name="Derniers matchs", value="\n".join(recent), inline=False)
    return embed


def simulation(report: SimulationReport) -> discord.Embed:
    weeks = [
        f"Semaine {w.number} · {w.matches} match(s) · {w.byes} exempt(s) · {w.upsets} surprise(s)"
        for w in report.weeks
    ]
    ranked = [e for e in report.leaderboard if not e.provisional]
    provisional = [e for e in report.leaderboard if e.provisional]
    embed = discord.Embed(
        title="🧪 Simulation terminée",
        description=_lines(weeks, "Aucune semaine simulée."),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Classement final",
        value=_lines([_entry_line(e) for e in ranked[:10]], "Aucun joueur classé."),
        inline=False,
    )
    if provisional:
        embed.add_field(
            name="En placement",
            value=_lines([_placement_line(e) for e in provisional[:10]], "—"),
            inline=False,
        )
    return embed


# --- Économie (V1 : conservée pour une version ultérieure) --------------------------------------


def richest(wallets: Sequence[Wallet], fake_names: Mapping[int, str]) -> discord.Embed:
    """``fake_names`` : noms des faux joueurs (/dev), sans compte Discord à mentionner."""

    def holder(user_id: int) -> str:
        return f"**{fake_names[user_id]}**" if user_id in fake_names else f"<@{user_id}>"

    lines = [
        f"{rank(i)} {holder(w.user_id)} · {amount(w.balance)}" for i, w in enumerate(wallets, 1)
    ]
    return discord.Embed(
        title="💎 Les plus riches",
        description=_lines(lines, "Personne n'a encore de portefeuille."),
        color=ECONOMY_COLOR,
    )


_BET_STATUS = {
    BetStatus.OPEN: "en cours",
    BetStatus.LOST: "perdu",
    BetStatus.REFUNDED: "remboursé",
}


def bets(summaries: Sequence[BetSummary]) -> discord.Embed:
    lines = []
    for s in summaries:
        picked = s.match.player(s.bet.side).display_name
        status = (
            f"gagné : {amount(s.bet.payout or 0)}"
            if s.bet.status is BetStatus.WON
            else _BET_STATUS[s.bet.status]
        )
        lines.append(
            f"`#{s.match.match.pk}` {amount(s.bet.amount)} sur **{picked}** "
            f"({s.match.player1.display_name} vs {s.match.player2.display_name}) · {status}"
        )
    return discord.Embed(
        title="🎲 Tes paris",
        description=_lines(lines, "Aucun pari pour l'instant : `/bet` pour commencer."),
        color=ECONOMY_COLOR,
    )


def pools(summary: MatchSummary, pool: Pool) -> str:
    """Texte de cagnotte, utilisé uniquement quand les paris sont activés."""
    if pool.total == 0:
        return "à jouer"
    quotes = [odds(pool.on(side), pool.total) for side in Side]
    quoted = " / ".join("—" if q is None else f"{q:.2f}" for q in quotes)
    return f"cagnotte {amount(pool.total)} · cotes {quoted}"
