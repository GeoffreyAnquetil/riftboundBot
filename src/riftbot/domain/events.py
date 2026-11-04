"""Événements de domaine : publiés par un module, écoutés par d'autres sans couplage direct.

Le ladder publie ces événements ; le module de paris les écoute pour régler les mises.
"""

from dataclasses import dataclass

from riftbot.domain.ladder.models import Side


@dataclass(frozen=True)
class MatchConfirmed:
    guild_id: int
    match_id: int
    winner_side: Side


@dataclass(frozen=True)
class MatchVoided:
    guild_id: int
    match_id: int
