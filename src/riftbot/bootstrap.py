"""Racine de composition : le seul endroit qui choisit les implémentations concrètes.

Pour changer de base de données, de règle de ladder ou de calcul des gains, c'est ici.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from riftbot.application.betting_service import BettingService
from riftbot.application.ladder_service import LadderService
from riftbot.application.ports import Clock, EventBus, UnitOfWork, UnitOfWorkFactory
from riftbot.config import Settings
from riftbot.domain.economy.payout import ParimutuelPayout
from riftbot.domain.ladder.models import MatchFormat
from riftbot.domain.ladder.rules import AdjacentPairsRule
from riftbot.infrastructure.clock import SystemClock
from riftbot.infrastructure.event_bus import LocalEventBus
from riftbot.infrastructure.sql.database import create_engine, create_session_factory
from riftbot.infrastructure.sql.uow import SqlUnitOfWork


@dataclass(frozen=True)
class Services:
    ladder: LadderService
    betting: BettingService


def build_services(
    settings: Settings,
    uow_factory: UnitOfWorkFactory,
    clock: Clock | None = None,
    events: EventBus | None = None,
) -> Services:
    clock = clock or SystemClock()
    events = events or LocalEventBus()
    ladder = LadderService(
        uow_factory,
        rule=AdjacentPairsRule(),  # MOCK : à remplacer par la vraie règle de ladder
        clock=clock,
        events=events,
        match_format=MatchFormat(best_of=settings.best_of),
        auto_confirm_after=timedelta(hours=settings.auto_confirm_hours),
    )
    betting = BettingService(
        uow_factory,
        payout=ParimutuelPayout(),
        clock=clock,
        starting_balance=settings.starting_balance,
    )
    betting.subscribe_to(events)
    return Services(ladder=ladder, betting=betting)


def build_sql_services(settings: Settings) -> tuple[Services, AsyncEngine]:
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    def uow_factory() -> UnitOfWork:
        return SqlUnitOfWork(session_factory)

    return build_services(settings, uow_factory), engine
