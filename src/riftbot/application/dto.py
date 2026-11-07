"""Objets renvoyés par les services à la couche de présentation (lecture seule)."""

from __future__ import annotations

from dataclasses import dataclass

from riftbot.domain.economy.models import Bet
from riftbot.domain.ladder.models import Match, MatchStatus, Player, Side, Week


@dataclass(frozen=True)
class MatchSummary:
    match: Match
    player1: Player
    player2: Player

    def player(self, side: Side) -> Player:
        return self.player1 if side is Side.ONE else self.player2

    @property
    def winner(self) -> Player | None:
        side = self.match.winner_side
        return None if side is None else self.player(side)

    def opponent_of(self, discord_id: int) -> Player:
        return self.player2 if self.player1.discord_id == discord_id else self.player1


@dataclass(frozen=True)
class ReportFingerprint:
    """Identité d'une déclaration de résultat, gravée dans les boutons.

    Les boutons persistants ne peuvent pas stocker d'objet : on met ces trois
    valeurs dans leur ``custom_id``. Au clic, on refuse d'agir si la déclaration
    courante ne correspond plus, ce qui évite de confirmer un score obsolète.
    """

    reported_by: int
    score1: int
    score2: int

    @classmethod
    def of(cls, match: Match) -> ReportFingerprint | None:
        """Empreinte de la déclaration courante, ou ``None`` si aucune."""
        if match.reported_by is None or match.score1 is None or match.score2 is None:
            return None
        return cls(
            reported_by=match.reported_by,
            score1=match.score1,
            score2=match.score2,
        )


@dataclass(frozen=True)
class Registration:
    """Résultat d'une inscription : le joueur et, si possible, son match de la semaine."""

    player: Player
    match: MatchSummary | None
    reactivated: bool


@dataclass(frozen=True)
class WeekOverview:
    week: Week
    matches: list[MatchSummary]
    bye: Player | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class LeaderboardEntry:
    player: Player
    wins: int
    losses: int
    provisional: bool
    inactive: bool
    placement_matches: int = 4

    @property
    def rating(self) -> float:
        return self.player.rating

    @property
    def deviation(self) -> float:
        return self.player.rating_deviation

    @property
    def rated_matches(self) -> int:
        return self.player.rated_matches


@dataclass(frozen=True)
class PlayerProfile:
    player: Player
    wins: int
    losses: int
    best_rank: int
    recent: list[MatchSummary]
    provisional: bool
    inactive: bool
    placement_matches: int = 4

    @property
    def rating(self) -> float:
        return self.player.rating

    @property
    def deviation(self) -> float:
        return self.player.rating_deviation

    @property
    def rated_matches(self) -> int:
        return self.player.rated_matches


@dataclass(frozen=True)
class ReportOutcome:
    summary: MatchSummary
    status: MatchStatus


@dataclass(frozen=True)
class RankChange:
    player: Player
    old_rank: int
    new_rank: int
    old_rating: float
    new_rating: float
    provisional: bool

    @property
    def delta(self) -> int:
        """Positif quand le joueur monte."""
        return self.old_rank - self.new_rank

    @property
    def rating_delta(self) -> float:
        return self.new_rating - self.old_rating


@dataclass(frozen=True)
class WeekClosing:
    week: Week
    ranking: list[RankChange]
    confirmed: int
    voided: int


@dataclass(frozen=True)
class Pool:
    side1: int = 0
    side2: int = 0
    bettors: int = 0

    @property
    def total(self) -> int:
        return self.side1 + self.side2

    def on(self, side: Side) -> int:
        return self.side1 if side is Side.ONE else self.side2


@dataclass(frozen=True)
class BetSummary:
    bet: Bet
    match: MatchSummary
