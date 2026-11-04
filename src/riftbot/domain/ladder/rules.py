"""Règles de ladder : comment apparier les joueurs, et comment le classement évolue.

Chaque règle est une stratégie interchangeable (``LadderRule``). Le reste de l'application ne
connaît que le protocole : changer de règle revient à injecter une autre classe dans
``bootstrap.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Pairing:
    higher: int  # id du joueur le mieux classé
    lower: int


@dataclass(frozen=True)
class Outcome:
    higher: int
    lower: int
    winner: int | None  # None : match non joué ou annulé

    def __post_init__(self) -> None:
        if self.winner not in (self.higher, self.lower, None):
            raise ValueError("le vainqueur doit être l'un des deux joueurs")


class LadderRule(Protocol):
    name: str

    def pair(self, ranking: Sequence[int], week_number: int) -> list[Pairing]:
        """Matchs de la semaine, à partir des ids de joueurs triés du 1er au dernier."""
        ...

    def apply(self, ranking: Sequence[int], outcomes: Iterable[Outcome]) -> list[int]:
        """Nouveau classement (ids triés) après les résultats de la semaine."""
        ...


class AdjacentPairsRule:
    """MOCK provisoire : 1 contre 2, 3 contre 4, etc.

    Le gagnant prend la meilleure des deux places. Avec un nombre impair de joueurs, le
    dernier est exempt. Un match non joué ne change rien.
    """

    name = "adjacent-pairs"

    def pair(self, ranking: Sequence[int], week_number: int) -> list[Pairing]:
        return [Pairing(ranking[i], ranking[i + 1]) for i in range(0, len(ranking) - 1, 2)]

    def apply(self, ranking: Sequence[int], outcomes: Iterable[Outcome]) -> list[int]:
        new_ranking = list(ranking)
        for outcome in outcomes:
            if outcome.winner != outcome.lower:
                continue
            # Un joueur retiré en cours de semaine n'est plus dans le classement.
            if outcome.higher not in new_ranking or outcome.lower not in new_ranking:
                continue
            i, j = new_ranking.index(outcome.higher), new_ranking.index(outcome.lower)
            if i < j:
                new_ranking[i], new_ranking[j] = new_ranking[j], new_ranking[i]
        return new_ranking
