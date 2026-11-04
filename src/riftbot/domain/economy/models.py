from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from riftbot.domain.entity import Entity
from riftbot.domain.errors import InsufficientFunds, InvalidAmount, InvalidMatchState
from riftbot.domain.ladder.models import Side


@dataclass(kw_only=True)
class Wallet(Entity):
    """Solde d'un membre du serveur. Les montants sont des entiers (jamais de float)."""

    guild_id: int
    user_id: int  # id Discord : on peut parier sans faire partie du ladder
    balance: int

    def debit(self, amount: int) -> None:
        if amount <= 0:
            raise InvalidAmount("Le montant doit être strictement positif.")
        if amount > self.balance:
            raise InsufficientFunds(f"Solde insuffisant : tu as {self.balance}.")
        self.balance -= amount

    def credit(self, amount: int) -> None:
        if amount < 0:
            raise InvalidAmount("Un crédit ne peut pas être négatif.")
        self.balance += amount


class TransactionReason(StrEnum):
    INITIAL_GRANT = "initial_grant"
    BET_STAKE = "bet_stake"
    BET_PAYOUT = "bet_payout"
    BET_REFUND = "bet_refund"


@dataclass(kw_only=True)
class Transaction(Entity):
    """Écriture du registre : chaque mouvement de solde en laisse une, on n'en supprime jamais."""

    guild_id: int
    user_id: int
    amount: int  # signé : négatif pour un débit
    reason: TransactionReason
    created_at: datetime
    ref: str | None = None  # ex. "bet:42"


class BetStatus(StrEnum):
    OPEN = "open"
    WON = "won"
    LOST = "lost"
    REFUNDED = "refunded"


@dataclass(kw_only=True)
class Bet(Entity):
    guild_id: int
    match_id: int
    user_id: int
    side: Side
    amount: int
    placed_at: datetime
    status: BetStatus = BetStatus.OPEN
    payout: int | None = None
    settled_at: datetime | None = None

    def settle(self, status: BetStatus, payout: int, at: datetime) -> None:
        if self.status is not BetStatus.OPEN:
            raise InvalidMatchState("Ce pari est déjà réglé.")
        if status is BetStatus.OPEN:
            raise ValueError("un pari réglé ne peut pas rester ouvert")
        self.status = status
        self.payout = payout
        self.settled_at = at
