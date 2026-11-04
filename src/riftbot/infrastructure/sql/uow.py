from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from riftbot.infrastructure.sql.repositories import (
    SqlBetRepository,
    SqlMatchRepository,
    SqlPlayerRepository,
    SqlRankingSnapshotRepository,
    SqlTransactionRepository,
    SqlWalletRepository,
    SqlWeekRepository,
    flush,
)


def _keep_strong_references(session: Session) -> None:
    """Garde en vie les lignes chargées tant que la session est ouverte.

    Par défaut, la session SQLAlchemy ne garde que des références faibles : une ligne lue puis
    convertie en entité du domaine serait relue à l'``update``, avec sa version la plus récente,
    et le verrouillage optimiste ne détecterait jamais de conflit. Recette issue de la doc
    SQLAlchemy (« Session Referencing Behavior »).
    """
    refs: set[Any] = set()

    @event.listens_for(session, "loaded_as_persistent")
    @event.listens_for(session, "pending_to_persistent")
    def _ref(_session: Session, instance: Any) -> None:
        refs.add(instance)

    session.info["strong_refs"] = refs


class SqlUnitOfWork:
    """Une session SQLAlchemy = une transaction. Sans ``commit()``, tout est annulé en sortie."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        _keep_strong_references(self._session.sync_session)
        self.players = SqlPlayerRepository(self._session)
        self.weeks = SqlWeekRepository(self._session)
        self.matches = SqlMatchRepository(self._session)
        self.snapshots = SqlRankingSnapshotRepository(self._session)
        self.wallets = SqlWalletRepository(self._session)
        self.transactions = SqlTransactionRepository(self._session)
        self.bets = SqlBetRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self._session.rollback()  # sans effet si commit() a déjà eu lieu
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await flush(self._session)
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
