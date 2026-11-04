"""Cas d'usage de l'économie : soldes, paris, règlement des gains.

Ce module ne connaît pas ``LadderService`` : il réagit aux événements ``MatchConfirmed`` et
``MatchVoided`` publiés sur le bus. Il lit les matchs et joueurs via les repositories.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence

from riftbot.application.dto import BetSummary, Pool
from riftbot.application.ports import Clock, EventBus, UnitOfWork, UnitOfWorkFactory
from riftbot.application.queries import require_match, summarize
from riftbot.domain.economy.models import Bet, BetStatus, Transaction, TransactionReason, Wallet
from riftbot.domain.economy.payout import BetResolution, PayoutStrategy, refund_all
from riftbot.domain.errors import (
    AlreadyBet,
    BettingClosed,
    ConcurrentModification,
    InvalidAmount,
    NotAllowed,
)
from riftbot.domain.events import MatchConfirmed, MatchVoided
from riftbot.domain.ladder.models import Side

SETTLE_ATTEMPTS = 3

type _Resolver = Callable[[Sequence[Bet]], list[BetResolution]]

_REASONS = {
    BetStatus.WON: TransactionReason.BET_PAYOUT,
    BetStatus.REFUNDED: TransactionReason.BET_REFUND,
}


class BettingService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        payout: PayoutStrategy,
        clock: Clock,
        starting_balance: int = 1000,
    ) -> None:
        self._uow = uow_factory
        self._payout = payout
        self._clock = clock
        self._starting_balance = starting_balance

    def subscribe_to(self, events: EventBus) -> None:
        events.subscribe(MatchConfirmed, self._on_match_confirmed)
        events.subscribe(MatchVoided, self._on_match_voided)

    # --- Soldes ------------------------------------------------------------------------------

    async def balance(self, guild_id: int, user_id: int) -> Wallet:
        async with self._uow() as uow:
            wallet = await self._wallet(uow, guild_id, user_id)
            await uow.commit()
        return wallet

    async def richest(self, guild_id: int, limit: int = 10) -> list[Wallet]:
        async with self._uow() as uow:
            return await uow.wallets.list_richest(guild_id, limit)

    # --- Paris -------------------------------------------------------------------------------

    async def place_bet(
        self, guild_id: int, user_id: int, match_id: int, side: Side, amount: int
    ) -> Bet:
        if amount <= 0:
            raise InvalidAmount("La mise doit être d'au moins 1.")
        async with self._uow() as uow:
            match = await require_match(uow, guild_id, match_id)
            week = await uow.weeks.get(match.week_id)
            if not match.is_open_for_bets or week is None or not week.is_open:
                raise BettingClosed(f"Les paris sont fermés pour le match #{match_id}.")

            bettor = await uow.players.get_by_discord_id(guild_id, user_id)
            if bettor is not None and match.involves(bettor.pk):
                raise NotAllowed("Tu ne peux pas parier sur ton propre match.")
            if await uow.bets.get_for_user(match_id, user_id) is not None:
                raise AlreadyBet(f"Tu as déjà parié sur le match #{match_id}.")

            wallet = await self._wallet(uow, guild_id, user_id)
            wallet.debit(amount)
            await uow.wallets.update(wallet)

            now = self._clock.now()
            bet = await uow.bets.add(
                Bet(
                    guild_id=guild_id,
                    match_id=match_id,
                    user_id=user_id,
                    side=side,
                    amount=amount,
                    placed_at=now,
                )
            )
            await uow.transactions.add(
                Transaction(
                    guild_id=guild_id,
                    user_id=user_id,
                    amount=-amount,
                    reason=TransactionReason.BET_STAKE,
                    created_at=now,
                    ref=f"bet:{bet.pk}",
                )
            )
            await uow.commit()
        return bet

    async def pools(self, match_ids: Collection[int]) -> dict[int, Pool]:
        """Cagnottes par match, pour l'affichage des cotes."""
        async with self._uow() as uow:
            bets = await uow.bets.list_for_matches(match_ids)
        pools: dict[int, Pool] = {}
        for match_id in match_ids:
            on_match = [b for b in bets if b.match_id == match_id]
            pools[match_id] = Pool(
                side1=sum(b.amount for b in on_match if b.side is Side.ONE),
                side2=sum(b.amount for b in on_match if b.side is Side.TWO),
                bettors=len(on_match),
            )
        return pools

    async def user_bets(self, guild_id: int, user_id: int, limit: int = 10) -> list[BetSummary]:
        async with self._uow() as uow:
            bets = await uow.bets.list_for_user(guild_id, user_id, limit)
            matches = [await require_match(uow, guild_id, b.match_id) for b in bets]
            summaries = await summarize(uow, matches)
        return [BetSummary(bet, summary) for bet, summary in zip(bets, summaries, strict=True)]

    # --- Règlement ---------------------------------------------------------------------------

    async def settle_match(self, match_id: int, winning_side: Side) -> None:
        """Règle les paris ouverts d'un match. Idempotent : un pari réglé n'est plus touché."""
        await self._settle(match_id, lambda bets: self._payout.resolve(bets, winning_side))

    async def refund_match(self, match_id: int) -> None:
        await self._settle(match_id, refund_all)

    async def _on_match_confirmed(self, event: MatchConfirmed) -> None:
        await self.settle_match(event.match_id, event.winner_side)

    async def _on_match_voided(self, event: MatchVoided) -> None:
        await self.refund_match(event.match_id)

    async def _settle(self, match_id: int, resolve: _Resolver) -> None:
        # Un pari concurrent peut modifier un portefeuille pendant le règlement : on rejoue.
        for attempt in range(1, SETTLE_ATTEMPTS + 1):
            try:
                await self._settle_once(match_id, resolve)
                return
            except ConcurrentModification:
                if attempt == SETTLE_ATTEMPTS:
                    raise

    async def _settle_once(self, match_id: int, resolve: _Resolver) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            bets = [
                b for b in await uow.bets.list_for_matches([match_id]) if b.status is BetStatus.OPEN
            ]
            if not bets:
                return
            by_id = {b.pk: b for b in bets}
            for resolution in resolve(bets):
                bet = by_id[resolution.bet_id]
                bet.settle(resolution.status, resolution.payout, now)
                await uow.bets.update(bet)
                if resolution.payout > 0:
                    wallet = await self._wallet(uow, bet.guild_id, bet.user_id)
                    wallet.credit(resolution.payout)
                    await uow.wallets.update(wallet)
                    await uow.transactions.add(
                        Transaction(
                            guild_id=bet.guild_id,
                            user_id=bet.user_id,
                            amount=resolution.payout,
                            reason=_REASONS[resolution.status],
                            created_at=now,
                            ref=f"bet:{bet.pk}",
                        )
                    )
            await uow.commit()

    async def _wallet(self, uow: UnitOfWork, guild_id: int, user_id: int) -> Wallet:
        """Portefeuille du membre, créé avec le solde de départ au premier accès."""
        wallet = await uow.wallets.get(guild_id, user_id)
        if wallet is not None:
            return wallet
        wallet = await uow.wallets.add(
            Wallet(guild_id=guild_id, user_id=user_id, balance=self._starting_balance)
        )
        await uow.transactions.add(
            Transaction(
                guild_id=guild_id,
                user_id=user_id,
                amount=self._starting_balance,
                reason=TransactionReason.INITIAL_GRANT,
                created_at=self._clock.now(),
            )
        )
        return wallet
