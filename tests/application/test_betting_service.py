from __future__ import annotations

import pytest

from riftbot.application.ports import UnitOfWorkFactory
from riftbot.bootstrap import Services
from riftbot.domain.economy.models import BetStatus, TransactionReason
from riftbot.domain.errors import (
    AlreadyBet,
    BettingClosed,
    InsufficientFunds,
    InvalidAmount,
    NotAllowed,
)
from riftbot.domain.ladder.models import Side
from tests.conftest import GUILD

ALICE, BOB = 1, 2  # joueurs
FAN1, FAN2, FAN3 = 101, 102, 103  # spectateurs


async def open_match(services: Services) -> int:
    for discord_id in (ALICE, BOB):
        await services.ladder.register(GUILD, discord_id, f"joueur{discord_id}")
    return (await services.ladder.open_week(GUILD)).matches[0].match.pk


async def assert_ledger_matches_balance(uow_factory: UnitOfWorkFactory, user_id: int) -> None:
    async with uow_factory() as uow:
        wallet = await uow.wallets.get(GUILD, user_id)
        transactions = await uow.transactions.list_for_user(GUILD, user_id)
    assert wallet is not None
    assert sum(t.amount for t in transactions) == wallet.balance


async def test_wallet_starts_with_initial_grant(
    services: Services, uow_factory: UnitOfWorkFactory
) -> None:
    assert (await services.betting.balance(GUILD, FAN1)).balance == 1000
    assert (await services.betting.balance(GUILD, FAN1)).balance == 1000  # pas de double crédit
    async with uow_factory() as uow:
        transactions = await uow.transactions.list_for_user(GUILD, FAN1)
    assert [t.reason for t in transactions] == [TransactionReason.INITIAL_GRANT]


async def test_place_bet_debits_wallet(services: Services) -> None:
    match_id = await open_match(services)
    bet = await services.betting.place_bet(GUILD, FAN1, match_id, Side.TWO, 300)
    assert bet.status is BetStatus.OPEN
    assert (await services.betting.balance(GUILD, FAN1)).balance == 700
    assert (await services.betting.pools([match_id]))[match_id].side2 == 300


async def test_bet_rules(services: Services) -> None:
    match_id = await open_match(services)
    betting = services.betting

    with pytest.raises(NotAllowed):
        await betting.place_bet(GUILD, ALICE, match_id, Side.ONE, 10)
    with pytest.raises(InsufficientFunds):
        await betting.place_bet(GUILD, FAN1, match_id, Side.ONE, 1001)
    with pytest.raises(InvalidAmount):
        await betting.place_bet(GUILD, FAN1, match_id, Side.ONE, 0)

    await betting.place_bet(GUILD, FAN1, match_id, Side.ONE, 10)
    with pytest.raises(AlreadyBet):
        await betting.place_bet(GUILD, FAN1, match_id, Side.TWO, 10)

    await services.ladder.report_result(GUILD, match_id, ALICE, 2, 0)
    with pytest.raises(BettingClosed):
        await betting.place_bet(GUILD, FAN2, match_id, Side.ONE, 10)


async def test_failed_bet_leaves_no_trace(services: Services) -> None:
    match_id = await open_match(services)
    with pytest.raises(InsufficientFunds):
        await services.betting.place_bet(GUILD, FAN1, match_id, Side.ONE, 5000)
    assert (await services.betting.pools([match_id]))[match_id].bettors == 0


async def test_confirmed_match_pays_winners(
    services: Services, uow_factory: UnitOfWorkFactory
) -> None:
    match_id = await open_match(services)
    betting = services.betting
    await betting.place_bet(GUILD, FAN1, match_id, Side.TWO, 100)
    await betting.place_bet(GUILD, FAN2, match_id, Side.ONE, 300)
    await betting.place_bet(GUILD, FAN3, match_id, Side.TWO, 100)

    await services.ladder.report_result(GUILD, match_id, BOB, 2, 1)
    await services.ladder.confirm_result(GUILD, match_id, ALICE)

    # cagnotte 500, côté Bob 200 : chacun récupère 250 pour 100 misés
    assert (await betting.balance(GUILD, FAN1)).balance == 1150
    assert (await betting.balance(GUILD, FAN2)).balance == 700
    assert (await betting.balance(GUILD, FAN3)).balance == 1150
    statuses = [b.bet.status for b in await betting.user_bets(GUILD, FAN2)]
    assert statuses == [BetStatus.LOST]
    for user in (FAN1, FAN2, FAN3):
        await assert_ledger_matches_balance(uow_factory, user)

    richest = await betting.richest(GUILD)
    assert [w.user_id for w in richest] == [FAN1, FAN3, FAN2]


async def test_unplayed_match_refunds_bets(
    services: Services, uow_factory: UnitOfWorkFactory
) -> None:
    match_id = await open_match(services)
    await services.betting.place_bet(GUILD, FAN1, match_id, Side.ONE, 250)

    await services.ladder.close_week(GUILD)

    assert (await services.betting.balance(GUILD, FAN1)).balance == 1000
    [summary] = await services.betting.user_bets(GUILD, FAN1)
    assert summary.bet.status is BetStatus.REFUNDED
    await assert_ledger_matches_balance(uow_factory, FAN1)


async def test_settlement_is_idempotent(services: Services) -> None:
    match_id = await open_match(services)
    await services.betting.place_bet(GUILD, FAN1, match_id, Side.ONE, 100)
    await services.ladder.set_result(GUILD, match_id, 2, 0)

    await services.betting.settle_match(match_id, Side.ONE)  # rejoué à la main

    assert (await services.betting.balance(GUILD, FAN1)).balance == 1000
