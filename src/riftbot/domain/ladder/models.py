from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum

from riftbot.domain.entity import Entity
from riftbot.domain.errors import InvalidMatchState, InvalidScore, NotAllowed


class Side(IntEnum):
    """Côté d'un match : ONE est le joueur le mieux noté au moment de l'appariement."""

    ONE = 1
    TWO = 2

    @property
    def other(self) -> Side:
        return Side.TWO if self is Side.ONE else Side.ONE


@dataclass(frozen=True)
class MatchFormat:
    """Format d'un match (BO1, BO3, BO5…)."""

    best_of: int = 3

    def __post_init__(self) -> None:
        if self.best_of < 1 or self.best_of % 2 == 0:
            raise ValueError("best_of doit être un entier impair positif")

    @property
    def wins_needed(self) -> int:
        return self.best_of // 2 + 1

    def winning_scores(self) -> list[tuple[int, int]]:
        """Scores possibles vus du vainqueur, du plus net au plus serré : [(2, 0), (2, 1)]."""
        return [(self.wins_needed, lost) for lost in range(self.wins_needed)]

    def validate(self, score1: int, score2: int) -> None:
        high, low = max(score1, score2), min(score1, score2)
        if low < 0 or high != self.wins_needed or low >= self.wins_needed:
            raise InvalidScore(f"Score {score1}-{score2} invalide pour un BO{self.best_of}.")


@dataclass(kw_only=True)
class Player(Entity):
    guild_id: int
    discord_id: int
    display_name: str
    rank: int
    registered_at: datetime
    active: bool = True
    # --- Note Glicko-2 ---------------------------------------------------------------------
    rating: float = 1500.0
    rating_deviation: float = 350.0
    rating_volatility: float = 0.06
    # Nombre de matchs notés depuis l'inscription. En dessous du seuil de placement, le
    # joueur n'est pas encore classé.
    rated_matches: int = 0
    # Semaines consécutives sans match confirmé : sert au badge « inactif ».
    inactive_weeks: int = 0
    # Nombre de semaines où le joueur a été exempt : sert à répartir les exempts équitablement.
    byes: int = 0
    last_match_at: datetime | None = None


class WeekStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(kw_only=True)
class Week(Entity):
    guild_id: int
    number: int
    opened_at: datetime
    status: WeekStatus = WeekStatus.OPEN
    closed_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.status is WeekStatus.OPEN

    def close(self, at: datetime) -> None:
        if not self.is_open:
            raise InvalidMatchState(f"La semaine {self.number} est déjà clôturée.")
        self.status = WeekStatus.CLOSED
        self.closed_at = at


class MatchStatus(StrEnum):
    PENDING = "pending"  # à jouer
    REPORTED = "reported"  # résultat déclaré par un joueur, en attente de l'adversaire
    CONFIRMED = "confirmed"  # résultat définitif
    DISPUTED = "disputed"  # résultats contradictoires : un admin doit trancher
    VOIDED = "voided"  # annulé (non joué, joueur retiré…)


@dataclass(kw_only=True)
class Match(Entity):
    """Un match entre deux joueurs, et sa machine à états.

    PENDING ──report──▶ REPORTED ──confirm / report concordant / auto──▶ CONFIRMED
       │                   │
       │                   └──dispute / report contradictoire──▶ DISPUTED
       └──────────────── resolve (admin) ──────────────────────────▶ CONFIRMED
    void : tout état non définitif ──▶ VOIDED
    """

    guild_id: int
    week_id: int
    player1_id: int
    player2_id: int
    status: MatchStatus = MatchStatus.PENDING
    score1: int | None = None
    score2: int | None = None
    reported_by: int | None = None
    reported_at: datetime | None = None
    resolved_at: datetime | None = None
    # Vrai une fois que la note Glicko-2 a été appliquée (à la clôture de la semaine).
    rated: bool = False

    # --- Lecture -----------------------------------------------------------------------------

    def involves(self, player_id: int) -> bool:
        return player_id in (self.player1_id, self.player2_id)

    def side_of(self, player_id: int) -> Side:
        if player_id == self.player1_id:
            return Side.ONE
        if player_id == self.player2_id:
            return Side.TWO
        raise NotAllowed("Tu ne joues pas ce match.")

    def player_id(self, side: Side) -> int:
        return self.player1_id if side is Side.ONE else self.player2_id

    @property
    def is_final(self) -> bool:
        return self.status in (MatchStatus.CONFIRMED, MatchStatus.VOIDED)

    @property
    def accepts_reports(self) -> bool:
        return self.status in (MatchStatus.PENDING, MatchStatus.REPORTED)

    @property
    def is_open_for_bets(self) -> bool:
        return self.status is MatchStatus.PENDING

    @property
    def winner_side(self) -> Side | None:
        if self.status is not MatchStatus.CONFIRMED or self.score1 is None or self.score2 is None:
            return None
        return Side.ONE if self.score1 > self.score2 else Side.TWO

    @property
    def winner_id(self) -> int | None:
        side = self.winner_side
        return None if side is None else self.player_id(side)

    @property
    def loser_id(self) -> int | None:
        side = self.winner_side
        return None if side is None else self.player_id(side.other)

    # --- Transitions -------------------------------------------------------------------------

    def report(
        self,
        reporter_id: int,
        own_score: int,
        opponent_score: int,
        fmt: MatchFormat,
        at: datetime,
    ) -> MatchStatus:
        """Un joueur déclare le résultat, de son point de vue.

        Si l'adversaire avait déjà déclaré le même résultat, le match est confirmé ; s'il avait
        déclaré l'inverse, le match passe en litige. Le déclarant peut corriger sa propre
        déclaration tant qu'elle n'est pas confirmée.
        """
        if self.is_final:
            raise InvalidMatchState("Le résultat de ce match est déjà validé.")
        if self.status is MatchStatus.DISPUTED:
            raise InvalidMatchState("Ce match est en litige : un admin doit trancher.")

        side = self.side_of(reporter_id)
        score1, score2 = (
            (own_score, opponent_score) if side is Side.ONE else (opponent_score, own_score)
        )
        fmt.validate(score1, score2)

        if self.status is MatchStatus.REPORTED and self.reported_by != reporter_id:
            if (score1, score2) == (self.score1, self.score2):
                self._confirm(at)
            else:
                self.status = MatchStatus.DISPUTED
            return self.status

        self.score1, self.score2 = score1, score2
        self.reported_by = reporter_id
        self.reported_at = at
        self.status = MatchStatus.REPORTED
        return self.status

    def confirm(self, player_id: int, at: datetime) -> None:
        self._require_opponent_of_reporter(player_id, "confirmer")
        self._confirm(at)

    def dispute(self, player_id: int) -> None:
        self._require_opponent_of_reporter(player_id, "contester")
        self.status = MatchStatus.DISPUTED

    def auto_confirm(self, at: datetime) -> None:
        if self.status is not MatchStatus.REPORTED:
            raise InvalidMatchState("Aucun résultat en attente de confirmation.")
        self._confirm(at)

    def resolve(self, score1: int, score2: int, fmt: MatchFormat, at: datetime) -> None:
        """Décision d'un admin : fixe le résultat quel que soit l'état non définitif."""
        if self.is_final:
            raise InvalidMatchState("Le résultat de ce match est déjà validé.")
        fmt.validate(score1, score2)
        self.score1, self.score2 = score1, score2
        self._confirm(at)

    def correct_score(self, score1: int, score2: int, fmt: MatchFormat) -> None:
        """Corrige le score d'un match validé sans changer le vainqueur.

        Sert aux corrections après la clôture de la semaine : les notes Glicko-2 ne
        dépendent que du vainqueur, pas du score, donc une correction à vainqueur
        constant ne fausse pas le classement déjà calculé.
        """
        if self.status is not MatchStatus.CONFIRMED:
            raise InvalidMatchState(
                "Seul un match validé peut être corrigé après la clôture de sa semaine."
            )
        fmt.validate(score1, score2)
        current_winner = self.winner_side
        new_winner = Side.ONE if score1 > score2 else Side.TWO
        if current_winner is None or new_winner is not current_winner:
            raise InvalidMatchState(
                "Changer le vainqueur après la clôture n'est pas possible : "
                "les notes de la semaine sont déjà appliquées."
            )
        self.score1, self.score2 = score1, score2

    def void(self, at: datetime) -> None:
        if self.is_final:
            raise InvalidMatchState("Le résultat de ce match est déjà validé.")
        self.status = MatchStatus.VOIDED
        self.resolved_at = at

    def _confirm(self, at: datetime) -> None:
        self.status = MatchStatus.CONFIRMED
        self.resolved_at = at

    def _require_opponent_of_reporter(self, player_id: int, action: str) -> None:
        if self.status is not MatchStatus.REPORTED:
            raise InvalidMatchState("Aucun résultat en attente de confirmation.")
        if not self.involves(player_id) or player_id == self.reported_by:
            raise NotAllowed(f"Seul l'adversaire du déclarant peut {action} ce résultat.")


@dataclass(kw_only=True)
class RankingSnapshot(Entity):
    """Position d'un joueur à l'ouverture d'une semaine, celle qui a servi aux appariements.

    Le rang est recalculé à la clôture, à l'inscription et au retrait : ces photos et le rang
    actuel suffisent à retracer tout l'historique.
    """

    week_id: int
    player_id: int
    rank: int
