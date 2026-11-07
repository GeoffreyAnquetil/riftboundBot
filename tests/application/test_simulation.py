from __future__ import annotations

import random

from riftbot.application.simulation import LadderSimulator
from riftbot.bootstrap import Services
from tests.conftest import GUILD


async def test_simulation_plays_and_closes_several_weeks(services: Services) -> None:
    ladder = services.ladder
    for discord_id in range(1, 7):
        await ladder.register(GUILD, discord_id, f"joueur{discord_id}")

    report = await LadderSimulator(ladder, random.Random(42)).run(GUILD, 3)

    assert [week.number for week in report.weeks] == [1, 2, 3]
    assert [week.matches for week in report.weeks] == [3, 3, 3]
    assert [week.byes for week in report.weeks] == [0, 0, 0]

    entries = await ladder.leaderboard(GUILD)
    assert len(entries) == 6
    assert all(entry.player.rated_matches == 3 for entry in entries)
    assert len({round(entry.rating, 1) for entry in entries}) > 1
    assert not any(entry.inactive for entry in entries)


async def test_simulation_counts_byes_with_an_odd_group(services: Services) -> None:
    ladder = services.ladder
    for discord_id in range(1, 6):
        await ladder.register(GUILD, discord_id, f"joueur{discord_id}")

    report = await LadderSimulator(ladder, random.Random(7)).run(GUILD, 2)

    assert [week.matches for week in report.weeks] == [2, 2]
    assert [week.byes for week in report.weeks] == [1, 1]
