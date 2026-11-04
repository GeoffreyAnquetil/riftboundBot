"""Implémentation en mémoire des ports de persistance.

Elle sert aux tests et au prototypage. Elle reproduit la sémantique de la version SQL : les
lectures renvoient des copies, et rien n'est visible des autres unités de travail avant
``commit()``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field, replace
from types import TracebackType
from typing import Any, Self

from riftbot.domain.economy.models import Bet, Transaction, Wallet
from riftbot.domain.entity import Entity
from riftbot.domain.ladder.models import (
    Match,
    MatchStatus,
    Player,
    RankingSnapshot,
    Week,
    WeekStatus,
)


@dataclass
class InMemoryStore:
    """Les « tables » partagées entre unités de travail."""

    tables: dict[str, dict[int, Any]] = field(default_factory=dict)
    sequences: dict[str, int] = field(default_factory=dict)

    def next_id(self, table: str) -> int:
        self.sequences[table] = self.sequences.get(table, 0) + 1
        return self.sequences[table]


class _Table[T: Entity]:
    def __init__(self, name: str, store: InMemoryStore, rows: dict[int, T]) -> None:
        self._name = name
        self._store = store
        self._rows = rows

    def insert(self, entity: T) -> T:
        stored = replace(entity, id=self._store.next_id(self._name))
        self._rows[stored.pk] = stored
        return copy.deepcopy(stored)

    def get(self, entity_id: int) -> T | None:
        row = self._rows.get(entity_id)
        return copy.deepcopy(row)

    def save(self, entity: T) -> None:
        if entity.pk not in self._rows:
            raise KeyError(f"{self._name}#{entity.pk} introuvable")
        self._rows[entity.pk] = copy.deepcopy(entity)

    def select(self, predicate: Callable[[T], bool] = lambda _: True) -> list[T]:
        return [copy.deepcopy(r) for r in self._rows.values() if predicate(r)]


class InMemoryPlayerRepository:
    def __init__(self, table: _Table[Player]) -> None:
        self._t = table

    async def add(self, player: Player) -> Player:
        return self._t.insert(player)

    async def get(self, player_id: int) -> Player | None:
        return self._t.get(player_id)

    async def get_many(self, player_ids: Collection[int]) -> dict[int, Player]:
        return {p.pk: p for p in self._t.select(lambda p: p.pk in player_ids)}

    async def get_by_discord_id(self, guild_id: int, discord_id: int) -> Player | None:
        found = self._t.select(lambda p: p.guild_id == guild_id and p.discord_id == discord_id)
        return found[0] if found else None

    async def list_active(self, guild_id: int) -> list[Player]:
        players = self._t.select(lambda p: p.guild_id == guild_id and p.active)
        return sorted(players, key=lambda p: p.rank)

    async def update(self, player: Player) -> None:
        self._t.save(player)


class InMemoryWeekRepository:
    def __init__(self, table: _Table[Week]) -> None:
        self._t = table

    async def add(self, week: Week) -> Week:
        return self._t.insert(week)

    async def get(self, week_id: int) -> Week | None:
        return self._t.get(week_id)

    async def get_open(self, guild_id: int) -> Week | None:
        found = self._t.select(lambda w: w.guild_id == guild_id and w.status is WeekStatus.OPEN)
        return found[0] if found else None

    async def get_latest(self, guild_id: int) -> Week | None:
        weeks = self._t.select(lambda w: w.guild_id == guild_id)
        return max(weeks, key=lambda w: w.number, default=None)

    async def update(self, week: Week) -> None:
        self._t.save(week)


class InMemoryMatchRepository:
    def __init__(self, table: _Table[Match]) -> None:
        self._t = table

    async def add(self, match: Match) -> Match:
        return self._t.insert(match)

    async def get(self, match_id: int) -> Match | None:
        return self._t.get(match_id)

    async def list_for_week(self, week_id: int) -> list[Match]:
        return self._sorted(self._t.select(lambda m: m.week_id == week_id))

    async def list_for_player(self, player_id: int) -> list[Match]:
        return self._sorted(self._t.select(lambda m: m.involves(player_id)))

    async def list_for_guild(self, guild_id: int, status: MatchStatus | None = None) -> list[Match]:
        return self._sorted(
            self._t.select(
                lambda m: m.guild_id == guild_id and (status is None or m.status is status)
            )
        )

    async def list_by_status(self, status: MatchStatus) -> list[Match]:
        return self._sorted(self._t.select(lambda m: m.status is status))

    async def update(self, match: Match) -> None:
        self._t.save(match)

    @staticmethod
    def _sorted(matches: list[Match]) -> list[Match]:
        return sorted(matches, key=lambda m: m.pk)


class InMemoryRankingSnapshotRepository:
    def __init__(self, table: _Table[RankingSnapshot]) -> None:
        self._t = table

    async def add_many(self, snapshots: Sequence[RankingSnapshot]) -> None:
        for snapshot in snapshots:
            self._t.insert(snapshot)

    async def list_for_player(self, player_id: int) -> list[RankingSnapshot]:
        return self._t.select(lambda s: s.player_id == player_id)


class InMemoryWalletRepository:
    def __init__(self, table: _Table[Wallet]) -> None:
        self._t = table

    async def add(self, wallet: Wallet) -> Wallet:
        return self._t.insert(wallet)

    async def get(self, guild_id: int, user_id: int) -> Wallet | None:
        found = self._t.select(lambda w: w.guild_id == guild_id and w.user_id == user_id)
        return found[0] if found else None

    async def list_richest(self, guild_id: int, limit: int) -> list[Wallet]:
        wallets = self._t.select(lambda w: w.guild_id == guild_id)
        return sorted(wallets, key=lambda w: (-w.balance, w.pk))[:limit]

    async def update(self, wallet: Wallet) -> None:
        self._t.save(wallet)


class InMemoryTransactionRepository:
    def __init__(self, table: _Table[Transaction]) -> None:
        self._t = table

    async def add(self, transaction: Transaction) -> Transaction:
        return self._t.insert(transaction)

    async def list_for_user(self, guild_id: int, user_id: int) -> list[Transaction]:
        return self._t.select(lambda t: t.guild_id == guild_id and t.user_id == user_id)


class InMemoryBetRepository:
    def __init__(self, table: _Table[Bet]) -> None:
        self._t = table

    async def add(self, bet: Bet) -> Bet:
        return self._t.insert(bet)

    async def get_for_user(self, match_id: int, user_id: int) -> Bet | None:
        found = self._t.select(lambda b: b.match_id == match_id and b.user_id == user_id)
        return found[0] if found else None

    async def list_for_matches(self, match_ids: Collection[int]) -> list[Bet]:
        return sorted(self._t.select(lambda b: b.match_id in match_ids), key=lambda b: b.pk)

    async def list_for_user(self, guild_id: int, user_id: int, limit: int) -> list[Bet]:
        bets = self._t.select(lambda b: b.guild_id == guild_id and b.user_id == user_id)
        return sorted(bets, key=lambda b: b.pk, reverse=True)[:limit]

    async def update(self, bet: Bet) -> None:
        self._t.save(bet)


class InMemoryUnitOfWork:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store
        self._working: dict[str, dict[int, Any]] = {}

    def _table[T: Entity](self, name: str) -> _Table[T]:
        return _Table(name, self._store, self._working.setdefault(name, {}))

    async def __aenter__(self) -> Self:
        self._bind()
        return self

    def _bind(self) -> None:
        """Prend une copie de travail des tables et y branche les repositories."""
        self._working = copy.deepcopy(self._store.tables)
        self.players = InMemoryPlayerRepository(self._table("players"))
        self.weeks = InMemoryWeekRepository(self._table("weeks"))
        self.matches = InMemoryMatchRepository(self._table("matches"))
        self.snapshots = InMemoryRankingSnapshotRepository(self._table("snapshots"))
        self.wallets = InMemoryWalletRepository(self._table("wallets"))
        self.transactions = InMemoryTransactionRepository(self._table("transactions"))
        self.bets = InMemoryBetRepository(self._table("bets"))

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._working = {}

    async def commit(self) -> None:
        self._store.tables = copy.deepcopy(self._working)

    async def rollback(self) -> None:
        self._bind()
