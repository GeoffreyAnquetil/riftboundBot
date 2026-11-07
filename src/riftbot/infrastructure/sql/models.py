"""Schéma SQL (SQLAlchemy). Ces classes restent dans l'infrastructure : le domaine ne les voit pas.

Toute modification de ce fichier doit être accompagnée d'une migration Alembic :
    uv run alembic revision --autogenerate -m "description"
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Stocke en UTC sans fuseau (SQLite n'en gère pas) et relit avec ``tzinfo=UTC``."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime sans fuseau horaire refusé")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        assert isinstance(value, datetime)
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime(), int: BigInteger()}


# BigInteger devient INTEGER sur SQLite : sans cela, les clés primaires ne s'auto-incrémentent pas.
PK = BigInteger().with_variant(Integer(), "sqlite")


class PlayerRow(Base):
    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("guild_id", "discord_id"),)

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    guild_id: Mapped[int] = mapped_column(index=True)
    discord_id: Mapped[int]
    display_name: Mapped[str] = mapped_column(String(100))
    rank: Mapped[int]
    registered_at: Mapped[datetime]
    active: Mapped[bool] = mapped_column(default=True)

    # Note Glicko-2. Les valeurs par défaut sont aussi côté serveur pour que la migration
    # puisse ajouter les colonnes à une base existante sans réécrire les lignes.
    rating: Mapped[float] = mapped_column(Float, default=1500.0, server_default="1500")
    rating_deviation: Mapped[float] = mapped_column(Float, default=350.0, server_default="350")
    rating_volatility: Mapped[float] = mapped_column(Float, default=0.06, server_default="0.06")
    rated_matches: Mapped[int] = mapped_column(default=0, server_default="0")
    inactive_weeks: Mapped[int] = mapped_column(default=0, server_default="0")
    byes: Mapped[int] = mapped_column(default=0, server_default="0")
    last_match_at: Mapped[datetime | None]


class WeekRow(Base):
    __tablename__ = "weeks"
    __table_args__ = (UniqueConstraint("guild_id", "number"),)

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    guild_id: Mapped[int] = mapped_column(index=True)
    number: Mapped[int]
    status: Mapped[str] = mapped_column(String(16))
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]


class MatchRow(Base):
    __tablename__ = "matches"
    __table_args__ = (Index("ix_matches_status", "status"),)

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    guild_id: Mapped[int] = mapped_column(index=True)
    week_id: Mapped[int] = mapped_column(ForeignKey("weeks.id"), index=True)
    player1_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    player2_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    status: Mapped[str] = mapped_column(String(16))
    score1: Mapped[int | None]
    score2: Mapped[int | None]
    reported_by: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    reported_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]
    rated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    version: Mapped[int] = mapped_column(default=1)

    # Verrouillage optimiste : deux confirmations simultanées ne peuvent pas s'écraser.
    __mapper_args__ = {"version_id_col": version}


class RankingSnapshotRow(Base):
    __tablename__ = "ranking_snapshots"
    __table_args__ = (UniqueConstraint("week_id", "player_id"),)

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    week_id: Mapped[int] = mapped_column(ForeignKey("weeks.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    rank: Mapped[int]


class WalletRow(Base):
    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("guild_id", "user_id"),)

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    guild_id: Mapped[int]
    user_id: Mapped[int]
    balance: Mapped[int]
    version: Mapped[int] = mapped_column(default=1)

    # Verrouillage optimiste : empêche une double dépense en cas de requêtes concurrentes.
    __mapper_args__ = {"version_id_col": version}


class TransactionRow(Base):
    __tablename__ = "transactions"
    __table_args__ = (Index("ix_transactions_user", "guild_id", "user_id"),)

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    guild_id: Mapped[int]
    user_id: Mapped[int]
    amount: Mapped[int]
    reason: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime]


class BetRow(Base):
    __tablename__ = "bets"
    __table_args__ = (
        UniqueConstraint("match_id", "user_id"),
        Index("ix_bets_user", "guild_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    guild_id: Mapped[int]
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    user_id: Mapped[int]
    side: Mapped[int]
    amount: Mapped[int]
    status: Mapped[str] = mapped_column(String(16))
    payout: Mapped[int | None]
    placed_at: Mapped[datetime]
    settled_at: Mapped[datetime | None]
