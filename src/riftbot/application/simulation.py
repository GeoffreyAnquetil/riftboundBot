"""Simulation de semaines pour tester le ladder seul.

Ce module n'est utilisé que par les commandes ``/dev``. Il joue des matchs réalistes (le
favori gagne plus souvent, mais les surprises restent possibles) puis clôture les semaines,
ce qui permet de vérifier en quelques secondes l'appariement, les notes et le classement.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from riftbot.application.dto import LeaderboardEntry, MatchSummary, WeekOverview
from riftbot.application.ladder_service import LadderService
from riftbot.domain.errors import WeekAlreadyOpen
from riftbot.domain.ladder.models import Player
from riftbot.domain.ladder.rating import Rating, expected_score


@dataclass(frozen=True)
class SimulatedWeek:
    number: int
    matches: int
    byes: int
    upsets: int


@dataclass(frozen=True)
class SimulationReport:
    weeks: list[SimulatedWeek]
    leaderboard: list[LeaderboardEntry]


class LadderSimulator:
    def __init__(self, ladder: LadderService, rng: random.Random | None = None) -> None:
        self._ladder = ladder
        self._rng = rng or random.Random()

    async def run(self, guild_id: int, weeks: int) -> SimulationReport:
        if weeks < 1:
            raise ValueError("weeks doit être au moins 1")
        simulated: list[SimulatedWeek] = []
        for _ in range(weeks):
            overview = await self._open_or_current(guild_id)
            upsets = 0
            for summary in overview.matches:
                if summary.match.is_final:
                    continue
                winner = self._pick_winner(summary)
                if winner.pk != summary.match.player1_id:
                    # ``player1`` est le mieux noté au moment du tirage : gagner en tant que
                    # joueur 2 est donc une surprise.
                    upsets += 1
                first_score, second_score = self._score(summary, winner.pk)
                await self._ladder.set_result(guild_id, summary.match.pk, first_score, second_score)
            await self._ladder.close_week(guild_id)
            simulated.append(
                SimulatedWeek(
                    number=overview.week.number,
                    matches=len(overview.matches),
                    byes=1 if overview.bye is not None else 0,
                    upsets=upsets,
                )
            )
        return SimulationReport(
            weeks=simulated,
            leaderboard=await self._ladder.leaderboard(guild_id),
        )

    async def _open_or_current(self, guild_id: int) -> WeekOverview:
        try:
            return await self._ladder.open_week(guild_id)
        except WeekAlreadyOpen:
            overview = await self._ladder.week_overview(guild_id)
            if overview is None or not overview.week.is_open:
                raise
            return overview

    def _pick_winner(self, summary: MatchSummary) -> Player:
        first = Rating(
            summary.player1.rating,
            summary.player1.rating_deviation,
            summary.player1.rating_volatility,
        )
        second = Rating(
            summary.player2.rating,
            summary.player2.rating_deviation,
            summary.player2.rating_volatility,
        )
        probability = expected_score(first, second)
        return summary.player1 if self._rng.random() < probability else summary.player2

    def _score(self, summary: MatchSummary, winner_id: int) -> tuple[int, int]:
        winner_score = 2
        loser_score = 0 if self._rng.random() < 0.6 else 1
        if winner_id == summary.match.player1_id:
            return winner_score, loser_score
        return loser_score, winner_score
