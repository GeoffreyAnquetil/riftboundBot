"""Requêtes de lecture partagées entre services."""

from collections.abc import Sequence

from riftbot.application.dto import MatchSummary
from riftbot.application.ports import UnitOfWork
from riftbot.domain.errors import NotFound
from riftbot.domain.ladder.models import Match


async def summarize(uow: UnitOfWork, matches: Sequence[Match]) -> list[MatchSummary]:
    ids = {pid for m in matches for pid in (m.player1_id, m.player2_id)}
    players = await uow.players.get_many(ids)
    return [MatchSummary(m, players[m.player1_id], players[m.player2_id]) for m in matches]


async def require_match(uow: UnitOfWork, guild_id: int, match_id: int) -> Match:
    match = await uow.matches.get(match_id)
    if match is None or match.guild_id != guild_id:
        raise NotFound(f"Le match #{match_id} n'existe pas.")
    return match
