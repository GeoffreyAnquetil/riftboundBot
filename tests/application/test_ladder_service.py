from __future__ import annotations

from datetime import timedelta

import pytest

from riftbot.application.ladder_service import LadderService
from riftbot.bootstrap import Services
from riftbot.domain.errors import (
    AlreadyRegistered,
    NoOpenWeek,
    NotAllowed,
    NotEnoughPlayers,
    NotRegistered,
    UnresolvedDisputes,
    WeekAlreadyOpen,
)
from riftbot.domain.ladder.models import MatchStatus
from tests.conftest import GUILD, FakeClock

ALICE, BOB, CAROL, DAVE = 1, 2, 3, 4


async def register_all(ladder: LadderService, *ids: int) -> None:
    for discord_id in ids:
        await ladder.register(GUILD, discord_id, f"joueur{discord_id}")


async def ranking(ladder: LadderService) -> list[int]:
    return [e.player.discord_id for e in await ladder.leaderboard(GUILD)]


async def test_register_appends_to_the_bottom(services: Services) -> None:
    await register_all(services.ladder, ALICE, BOB)
    assert await ranking(services.ladder) == [ALICE, BOB]
    with pytest.raises(AlreadyRegistered):
        await services.ladder.register(GUILD, ALICE, "alice")


async def test_guilds_are_isolated(services: Services) -> None:
    await register_all(services.ladder, ALICE)
    assert await services.ladder.leaderboard(GUILD + 1) == []


async def test_open_week_pairs_adjacent_players(services: Services) -> None:
    ladder = services.ladder
    with pytest.raises(NotEnoughPlayers):
        await ladder.open_week(GUILD)
    await register_all(ladder, ALICE, BOB, CAROL, DAVE)

    overview = await ladder.open_week(GUILD)

    assert overview.week.number == 1
    pairs = [(s.player1.discord_id, s.player2.discord_id) for s in overview.matches]
    assert pairs == [(ALICE, BOB), (CAROL, DAVE)]
    with pytest.raises(WeekAlreadyOpen):
        await ladder.open_week(GUILD)


async def test_full_week_updates_the_ranking(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB, CAROL, DAVE)
    overview = await ladder.open_week(GUILD)
    match_ab, match_cd = (s.match.pk for s in overview.matches)

    # Bob (2e) bat Alice (1re), confirmé par Alice
    outcome = await ladder.report_result(GUILD, match_ab, BOB, 2, 1)
    assert outcome.status is MatchStatus.REPORTED
    await ladder.confirm_result(GUILD, match_ab, ALICE)
    # Carol (3e) bat Dave (4e) : déclarations concordantes
    await ladder.report_result(GUILD, match_cd, CAROL, 2, 0)
    assert (await ladder.report_result(GUILD, match_cd, DAVE, 0, 2)).status is (
        MatchStatus.CONFIRMED
    )

    closing = await ladder.close_week(GUILD)

    assert [c.player.discord_id for c in closing.ranking] == [BOB, ALICE, CAROL, DAVE]
    assert [c.delta for c in closing.ranking] == [1, -1, 0, 0]
    assert await ranking(ladder) == [BOB, ALICE, CAROL, DAVE]
    board = {e.player.discord_id: (e.wins, e.losses) for e in await ladder.leaderboard(GUILD)}
    assert board == {BOB: (1, 0), ALICE: (0, 1), CAROL: (1, 0), DAVE: (0, 1)}

    next_week = await ladder.open_week(GUILD)
    assert next_week.week.number == 2
    assert [(s.player1.discord_id, s.player2.discord_id) for s in next_week.matches] == [
        (BOB, ALICE),
        (CAROL, DAVE),
    ]


async def test_close_week_confirms_pending_reports_and_voids_unplayed(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB, CAROL, DAVE)
    overview = await ladder.open_week(GUILD)
    match_ab, match_cd = (s.match.pk for s in overview.matches)
    await ladder.report_result(GUILD, match_ab, BOB, 2, 0)  # jamais confirmé par Alice

    closing = await ladder.close_week(GUILD)

    assert (closing.confirmed, closing.voided) == (1, 1)
    assert (await ladder.get_match(GUILD, match_ab)).match.status is MatchStatus.CONFIRMED
    assert (await ladder.get_match(GUILD, match_cd)).match.status is MatchStatus.VOIDED
    assert await ranking(ladder) == [BOB, ALICE, CAROL, DAVE]
    with pytest.raises(NoOpenWeek):
        await ladder.close_week(GUILD)


async def test_dispute_blocks_closing_until_an_admin_decides(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)
    match_id = (await ladder.open_week(GUILD)).matches[0].match.pk
    await ladder.report_result(GUILD, match_id, ALICE, 2, 0)
    await ladder.dispute_result(GUILD, match_id, BOB)

    with pytest.raises(UnresolvedDisputes):
        await ladder.close_week(GUILD)

    await ladder.set_result(GUILD, match_id, 0, 2)
    await ladder.close_week(GUILD)
    assert await ranking(ladder) == [BOB, ALICE]


async def test_only_the_opponent_can_confirm(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB, CAROL)
    match_id = (await ladder.open_week(GUILD)).matches[0].match.pk
    await ladder.report_result(GUILD, match_id, ALICE, 2, 0)
    with pytest.raises(NotAllowed):
        await ladder.confirm_result(GUILD, match_id, ALICE)
    with pytest.raises(NotAllowed):
        await ladder.confirm_result(GUILD, match_id, CAROL)


async def test_stale_reports_are_auto_confirmed(services: Services, clock: FakeClock) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)
    match_id = (await ladder.open_week(GUILD)).matches[0].match.pk
    await ladder.report_result(GUILD, match_id, BOB, 2, 1)

    clock.advance(timedelta(hours=23))
    assert await ladder.auto_confirm_stale() == []
    clock.advance(timedelta(hours=1))
    assert [m.pk for m in await ladder.auto_confirm_stale()] == [match_id]
    assert (await ladder.get_match(GUILD, match_id)).match.status is MatchStatus.CONFIRMED


async def test_current_match(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB, CAROL)
    await ladder.open_week(GUILD)
    summary = await ladder.current_match(GUILD, BOB)
    assert summary.opponent_of(BOB).discord_id == ALICE
    with pytest.raises(Exception, match="pas de match"):
        await ladder.current_match(GUILD, CAROL)  # exempt cette semaine


async def test_remove_player_voids_match_and_compacts_ranks(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB, CAROL)
    match_id = (await ladder.open_week(GUILD)).matches[0].match.pk

    await ladder.remove_player(GUILD, ALICE)

    assert await ranking(ladder) == [BOB, CAROL]
    assert [e.player.rank for e in await ladder.leaderboard(GUILD)] == [1, 2]
    assert (await ladder.get_match(GUILD, match_id)).match.status is MatchStatus.VOIDED
    with pytest.raises(NotRegistered):
        await ladder.current_match(GUILD, ALICE)

    await ladder.register(GUILD, ALICE, "alice")
    assert await ranking(ladder) == [BOB, CAROL, ALICE]


async def test_profile(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)
    match_id = (await ladder.open_week(GUILD)).matches[0].match.pk
    await ladder.set_result(GUILD, match_id, 0, 2)
    await ladder.close_week(GUILD)

    bob = await ladder.profile(GUILD, BOB)
    alice = await ladder.profile(GUILD, ALICE)

    assert (bob.wins, bob.losses, bob.player.rank, bob.best_rank) == (1, 0, 1, 1)
    assert (alice.wins, alice.losses, alice.player.rank, alice.best_rank) == (0, 1, 2, 1)
    assert [s.match.pk for s in bob.recent] == [match_id]
