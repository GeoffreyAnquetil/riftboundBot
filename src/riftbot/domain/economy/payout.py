"""Calcul des gains des paris : fonctions pures, stratégie interchangeable."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from riftbot.domain.economy.models import Bet, BetStatus
from riftbot.domain.ladder.models import Side


@dataclass(frozen=True)
class BetResolution:
    bet_id: int
    status: BetStatus
    payout: int  # montant recrédité (mise comprise), 0 si perdu


class PayoutStrategy(Protocol):
    def resolve(self, bets: Sequence[Bet], winning_side: Side) -> list[BetResolution]: ...


def refund_all(bets: Sequence[Bet]) -> list[BetResolution]:
    return [BetResolution(b.pk, BetStatus.REFUNDED, b.amount) for b in bets]


class ParimutuelPayout:
    """Pari mutuel : la cagnotte totale est partagée entre les gagnants au prorata des mises.

    Aucune monnaie n'est créée : la somme des gains est exactement égale à la cagnotte. Les
    unités perdues par l'arrondi sont attribuées selon la méthode du plus fort reste (à égalité,
    au pari le plus ancien). Si personne n'a misé sur le vainqueur, tout le monde est remboursé.
    """

    def resolve(self, bets: Sequence[Bet], winning_side: Side) -> list[BetResolution]:
        winners = [b for b in bets if b.side is winning_side]
        if not winners:
            return refund_all(bets)

        pool = sum(b.amount for b in bets)
        winning_pool = sum(b.amount for b in winners)

        payouts = {b.pk: pool * b.amount // winning_pool for b in winners}
        leftover = pool - sum(payouts.values())
        by_remainder = sorted(winners, key=lambda b: (-(pool * b.amount % winning_pool), b.pk))
        for bet in by_remainder[:leftover]:
            payouts[bet.pk] += 1

        return [
            BetResolution(b.pk, BetStatus.WON, payouts[b.pk])
            if b.side is winning_side
            else BetResolution(b.pk, BetStatus.LOST, 0)
            for b in bets
        ]


def odds(side_pool: int, total_pool: int) -> float | None:
    """Cote indicative d'un côté (gain par unité misée), ou None si personne n'y a misé."""
    return None if side_pool == 0 else total_pool / side_pool
