"""Repositories SQLAlchemy : traduisent les lignes SQL en entités du domaine, et inversement."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from riftbot.domain.economy.models import Bet, BetStatus, Transaction, TransactionReason, Wallet
from riftbot.domain.errors import ConcurrentModification
from riftbot.domain.ladder.models import (
    Match,
    MatchStatus,
    Player,
    RankingSnapshot,
    Side,
    Week,
    WeekStatus,
)
from riftbot.infrastructure.sql.models import (
    BetRow,
    MatchRow,
    PlayerRow,
    RankingSnapshotRow,
    TransactionRow,
    WalletRow,
    WeekRow,
)


async def flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except StaleDataError as exc:
        raise ConcurrentModification() from exc


async def _row[R](session: AsyncSession, model: type[R], entity_id: int) -> R:
    row = await session.get(model, entity_id)
    if row is None:
        raise LookupError(f"{model.__name__}#{entity_id} introuvable")
    return row


# --- Ladder -------------------------------------------------------------------------------------


def _player(row: PlayerRow) -> Player:
    return Player(
        id=row.id,
        guild_id=row.guild_id,
        discord_id=row.discord_id,
        display_name=row.display_name,
        rank=row.rank,
        registered_at=row.registered_at,
        active=row.active,
        rating=row.rating,
        rating_deviation=row.rating_deviation,
        rating_volatility=row.rating_volatility,
        rated_matches=row.rated_matches,
        inactive_weeks=row.inactive_weeks,
        byes=row.byes,
        last_match_at=row.last_match_at,
    )


class SqlPlayerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, player: Player) -> Player:
        row = PlayerRow(
            guild_id=player.guild_id,
            discord_id=player.discord_id,
            display_name=player.display_name,
            rank=player.rank,
            registered_at=player.registered_at,
            active=player.active,
            rating=player.rating,
            rating_deviation=player.rating_deviation,
            rating_volatility=player.rating_volatility,
            rated_matches=player.rated_matches,
            inactive_weeks=player.inactive_weeks,
            byes=player.byes,
            last_match_at=player.last_match_at,
        )
        self._s.add(row)
        await flush(self._s)
        return _player(row)

    async def get(self, player_id: int) -> Player | None:
        row = await self._s.get(PlayerRow, player_id)
        return None if row is None else _player(row)

    async def get_many(self, player_ids: Collection[int]) -> dict[int, Player]:
        if not player_ids:
            return {}
        rows = await self._s.scalars(select(PlayerRow).where(PlayerRow.id.in_(player_ids)))
        return {row.id: _player(row) for row in rows}

    async def get_by_discord_id(self, guild_id: int, discord_id: int) -> Player | None:
        row = await self._s.scalar(
            select(PlayerRow).where(
                PlayerRow.guild_id == guild_id, PlayerRow.discord_id == discord_id
            )
        )
        return None if row is None else _player(row)

    async def list_active(self, guild_id: int) -> list[Player]:
        rows = await self._s.scalars(
            select(PlayerRow)
            .where(PlayerRow.guild_id == guild_id, PlayerRow.active.is_(True))
            .order_by(PlayerRow.rank)
        )
        return [_player(row) for row in rows]

    async def update(self, player: Player) -> None:
        row = await _row(self._s, PlayerRow, player.pk)
        row.display_name = player.display_name
        row.rank = player.rank
        row.active = player.active
        row.rating = player.rating
        row.rating_deviation = player.rating_deviation
        row.rating_volatility = player.rating_volatility
        row.rated_matches = player.rated_matches
        row.inactive_weeks = player.inactive_weeks
        row.byes = player.byes
        row.last_match_at = player.last_match_at
        await flush(self._s)


def _week(row: WeekRow) -> Week:
    return Week(
        id=row.id,
        guild_id=row.guild_id,
        number=row.number,
        status=WeekStatus(row.status),
        opened_at=row.opened_at,
        closed_at=row.closed_at,
    )


class SqlWeekRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, week: Week) -> Week:
        row = WeekRow(
            guild_id=week.guild_id,
            number=week.number,
            status=week.status.value,
            opened_at=week.opened_at,
            closed_at=week.closed_at,
        )
        self._s.add(row)
        await flush(self._s)
        return _week(row)

    async def get(self, week_id: int) -> Week | None:
        row = await self._s.get(WeekRow, week_id)
        return None if row is None else _week(row)

    async def get_open(self, guild_id: int) -> Week | None:
        row = await self._s.scalar(
            select(WeekRow).where(
                WeekRow.guild_id == guild_id, WeekRow.status == WeekStatus.OPEN.value
            )
        )
        return None if row is None else _week(row)

    async def get_latest(self, guild_id: int) -> Week | None:
        row = await self._s.scalar(
            select(WeekRow)
            .where(WeekRow.guild_id == guild_id)
            .order_by(WeekRow.number.desc())
            .limit(1)
        )
        return None if row is None else _week(row)

    async def update(self, week: Week) -> None:
        row = await _row(self._s, WeekRow, week.pk)
        row.status = week.status.value
        row.closed_at = week.closed_at
        await flush(self._s)


def _match(row: MatchRow) -> Match:
    return Match(
        id=row.id,
        guild_id=row.guild_id,
        week_id=row.week_id,
        player1_id=row.player1_id,
        player2_id=row.player2_id,
        status=MatchStatus(row.status),
        score1=row.score1,
        score2=row.score2,
        reported_by=row.reported_by,
        reported_at=row.reported_at,
        resolved_at=row.resolved_at,
        rated=row.rated,
    )


class SqlMatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, match: Match) -> Match:
        row = MatchRow(
            guild_id=match.guild_id,
            week_id=match.week_id,
            player1_id=match.player1_id,
            player2_id=match.player2_id,
            status=match.status.value,
            score1=match.score1,
            score2=match.score2,
            reported_by=match.reported_by,
            reported_at=match.reported_at,
            resolved_at=match.resolved_at,
            rated=match.rated,
        )
        self._s.add(row)
        await flush(self._s)
        return _match(row)

    async def get(self, match_id: int) -> Match | None:
        row = await self._s.get(MatchRow, match_id)
        return None if row is None else _match(row)

    async def list_for_week(self, week_id: int) -> list[Match]:
        return await self._list(MatchRow.week_id == week_id)

    async def list_for_player(self, player_id: int) -> list[Match]:
        return await self._list(
            or_(MatchRow.player1_id == player_id, MatchRow.player2_id == player_id)
        )

    async def list_for_guild(self, guild_id: int, status: MatchStatus | None = None) -> list[Match]:
        if status is None:
            return await self._list(MatchRow.guild_id == guild_id)
        return await self._list(MatchRow.guild_id == guild_id, MatchRow.status == status.value)

    async def list_by_status(self, status: MatchStatus) -> list[Match]:
        return await self._list(MatchRow.status == status.value)

    async def update(self, match: Match) -> None:
        row = await _row(self._s, MatchRow, match.pk)
        row.status = match.status.value
        row.score1 = match.score1
        row.score2 = match.score2
        row.reported_by = match.reported_by
        row.reported_at = match.reported_at
        row.resolved_at = match.resolved_at
        row.rated = match.rated
        await flush(self._s)

    async def _list(self, *criteria: ColumnElement[bool]) -> list[Match]:
        rows = await self._s.scalars(select(MatchRow).where(*criteria).order_by(MatchRow.id))
        return [_match(row) for row in rows]


def _snapshot(row: RankingSnapshotRow) -> RankingSnapshot:
    return RankingSnapshot(id=row.id, week_id=row.week_id, player_id=row.player_id, rank=row.rank)


class SqlRankingSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_many(self, snapshots: Sequence[RankingSnapshot]) -> None:
        self._s.add_all(
            RankingSnapshotRow(week_id=s.week_id, player_id=s.player_id, rank=s.rank)
            for s in snapshots
        )
        await flush(self._s)

    async def list_for_player(self, player_id: int) -> list[RankingSnapshot]:
        rows = await self._s.scalars(
            select(RankingSnapshotRow)
            .where(RankingSnapshotRow.player_id == player_id)
            .order_by(RankingSnapshotRow.id)
        )
        return [_snapshot(row) for row in rows]


# --- Économie -----------------------------------------------------------------------------------


def _wallet(row: WalletRow) -> Wallet:
    return Wallet(id=row.id, guild_id=row.guild_id, user_id=row.user_id, balance=row.balance)


class SqlWalletRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, wallet: Wallet) -> Wallet:
        row = WalletRow(guild_id=wallet.guild_id, user_id=wallet.user_id, balance=wallet.balance)
        self._s.add(row)
        await flush(self._s)
        return _wallet(row)

    async def get(self, guild_id: int, user_id: int) -> Wallet | None:
        row = await self._s.scalar(
            select(WalletRow).where(WalletRow.guild_id == guild_id, WalletRow.user_id == user_id)
        )
        return None if row is None else _wallet(row)

    async def list_richest(self, guild_id: int, limit: int) -> list[Wallet]:
        rows = await self._s.scalars(
            select(WalletRow)
            .where(WalletRow.guild_id == guild_id)
            .order_by(WalletRow.balance.desc(), WalletRow.id)
            .limit(limit)
        )
        return [_wallet(row) for row in rows]

    async def update(self, wallet: Wallet) -> None:
        row = await _row(self._s, WalletRow, wallet.pk)
        row.balance = wallet.balance
        await flush(self._s)


def _transaction(row: TransactionRow) -> Transaction:
    return Transaction(
        id=row.id,
        guild_id=row.guild_id,
        user_id=row.user_id,
        amount=row.amount,
        reason=TransactionReason(row.reason),
        ref=row.ref,
        created_at=row.created_at,
    )


class SqlTransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, transaction: Transaction) -> Transaction:
        row = TransactionRow(
            guild_id=transaction.guild_id,
            user_id=transaction.user_id,
            amount=transaction.amount,
            reason=transaction.reason.value,
            ref=transaction.ref,
            created_at=transaction.created_at,
        )
        self._s.add(row)
        await flush(self._s)
        return _transaction(row)

    async def list_for_user(self, guild_id: int, user_id: int) -> list[Transaction]:
        rows = await self._s.scalars(
            select(TransactionRow)
            .where(TransactionRow.guild_id == guild_id, TransactionRow.user_id == user_id)
            .order_by(TransactionRow.id)
        )
        return [_transaction(row) for row in rows]


def _bet(row: BetRow) -> Bet:
    return Bet(
        id=row.id,
        guild_id=row.guild_id,
        match_id=row.match_id,
        user_id=row.user_id,
        side=Side(row.side),
        amount=row.amount,
        status=BetStatus(row.status),
        payout=row.payout,
        placed_at=row.placed_at,
        settled_at=row.settled_at,
    )


class SqlBetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, bet: Bet) -> Bet:
        row = BetRow(
            guild_id=bet.guild_id,
            match_id=bet.match_id,
            user_id=bet.user_id,
            side=int(bet.side),
            amount=bet.amount,
            status=bet.status.value,
            payout=bet.payout,
            placed_at=bet.placed_at,
            settled_at=bet.settled_at,
        )
        self._s.add(row)
        await flush(self._s)
        return _bet(row)

    async def get_for_user(self, match_id: int, user_id: int) -> Bet | None:
        row = await self._s.scalar(
            select(BetRow).where(BetRow.match_id == match_id, BetRow.user_id == user_id)
        )
        return None if row is None else _bet(row)

    async def list_for_matches(self, match_ids: Collection[int]) -> list[Bet]:
        if not match_ids:
            return []
        rows = await self._s.scalars(
            select(BetRow).where(BetRow.match_id.in_(match_ids)).order_by(BetRow.id)
        )
        return [_bet(row) for row in rows]

    async def list_for_user(self, guild_id: int, user_id: int, limit: int) -> list[Bet]:
        rows = await self._s.scalars(
            select(BetRow)
            .where(BetRow.guild_id == guild_id, BetRow.user_id == user_id)
            .order_by(BetRow.id.desc())
            .limit(limit)
        )
        return [_bet(row) for row in rows]

    async def update(self, bet: Bet) -> None:
        row = await _row(self._s, BetRow, bet.pk)
        row.status = bet.status.value
        row.payout = bet.payout
        row.settled_at = bet.settled_at
        await flush(self._s)
