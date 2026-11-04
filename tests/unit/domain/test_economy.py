import random
from datetime import UTC, datetime

import pytest

from riftbot.domain.economy.models import Bet, BetStatus, Wallet
from riftbot.domain.economy.payout import ParimutuelPayout, odds
from riftbot.domain.errors import InsufficientFunds, InvalidAmount
from riftbot.domain.ladder.models import Side

NOW = datetime(2026, 11, 4, tzinfo=UTC)
payout = ParimutuelPayout()


def bet(bet_id: int, side: Side, amount: int) -> Bet:
    return Bet(
        id=bet_id, guild_id=1, match_id=1, user_id=bet_id, side=side, amount=amount, placed_at=NOW
    )


def test_wallet_debit_and_credit() -> None:
    wallet = Wallet(guild_id=1, user_id=1, balance=100)
    wallet.debit(30)
    wallet.credit(5)
    assert wallet.balance == 75


def test_wallet_refuses_overdraft_and_invalid_amounts() -> None:
    wallet = Wallet(guild_id=1, user_id=1, balance=100)
    with pytest.raises(InsufficientFunds):
        wallet.debit(101)
    with pytest.raises(InvalidAmount):
        wallet.debit(0)
    with pytest.raises(InvalidAmount):
        wallet.credit(-1)


def test_pool_is_shared_among_winners_with_largest_remainder() -> None:
    bets = [bet(1, Side.ONE, 100), bet(2, Side.TWO, 50), bet(3, Side.ONE, 50)]
    result = {r.bet_id: r for r in payout.resolve(bets, Side.ONE)}
    # cagnotte 200, côté gagnant 150 : 133,33 et 66,67, l'unité restante va au plus fort reste
    assert result[1].payout == 133
    assert result[3].payout == 67
    assert result[2].payout == 0
    assert result[2].status is BetStatus.LOST
    assert result[1].status is BetStatus.WON


def test_everyone_is_refunded_when_nobody_backed_the_winner() -> None:
    bets = [bet(1, Side.ONE, 100), bet(2, Side.ONE, 40)]
    result = payout.resolve(bets, Side.TWO)
    assert [(r.status, r.payout) for r in result] == [
        (BetStatus.REFUNDED, 100),
        (BetStatus.REFUNDED, 40),
    ]


def test_no_money_is_created_or_destroyed() -> None:
    rng = random.Random(42)
    for _ in range(500):
        bets = [
            bet(i, rng.choice(list(Side)), rng.randint(1, 1000)) for i in range(rng.randint(1, 12))
        ]
        winning_side = rng.choice(list(Side))
        total_paid = sum(r.payout for r in payout.resolve(bets, winning_side))
        assert total_paid == sum(b.amount for b in bets)


def test_odds() -> None:
    assert odds(50, 200) == 4.0
    assert odds(0, 200) is None
