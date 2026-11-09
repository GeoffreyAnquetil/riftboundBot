from __future__ import annotations

from datetime import timedelta

import pytest

from riftbot.application.dto import ReportFingerprint, WeekClosing
from riftbot.application.ladder_service import LadderService
from riftbot.application.ports import UnitOfWorkFactory
from riftbot.bootstrap import Services
from riftbot.domain.errors import (
    AlreadyRegistered,
    InvalidMatchState,
    NoOpenWeek,
    NotAllowed,
    NotEnoughPlayers,
    NotRegistered,
    StaleReport,
    UnresolvedDisputes,
    WeekAlreadyOpen,
)
from riftbot.domain.ladder.models import MatchStatus, Player
from tests.conftest import GUILD, FakeClock

ALICE, BOB, CAROL, DAVE = 1, 2, 3, 4


async def register_all(ladder: LadderService, *ids: int) -> None:
    for discord_id in ids:
        await ladder.register(GUILD, discord_id, f"joueur{discord_id}")


async def ranking(ladder: LadderService) -> list[int]:
    return [e.player.discord_id for e in await ladder.leaderboard(GUILD)]


async def set_rating(uow_factory: UnitOfWorkFactory, discord_id: int, rating: float) -> None:
    async with uow_factory() as uow:
        player = await uow.players.get_by_discord_id(GUILD, discord_id)
        assert player is not None
        player.rating = rating
        await uow.players.update(player)
        await uow.commit()


async def get_player(uow_factory: UnitOfWorkFactory, discord_id: int) -> Player:
    async with uow_factory() as uow:
        player = await uow.players.get_by_discord_id(GUILD, discord_id)
    assert player is not None
    return player


async def play_week(ladder: LadderService, winner: int | None = None) -> WeekClosing:
    overview = await ladder.open_week(GUILD)
    for summary in overview.matches:
        if winner in (summary.player1.discord_id, summary.player2.discord_id):
            chosen = winner
        else:
            chosen = summary.player1.discord_id
        score = (2, 0) if chosen == summary.player1.discord_id else (0, 2)
        await ladder.set_result(GUILD, summary.match.pk, *score)
    return await ladder.close_week(GUILD)


async def test_register_starts_provisional_and_at_the_bottom(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)

    entries = await ladder.leaderboard(GUILD)
    assert [e.player.discord_id for e in entries] == [ALICE, BOB]
    assert all(e.provisional for e in entries)
    assert [e.player.rated_matches for e in entries] == [0, 0]
    assert [e.player.rating for e in entries] == [1500.0, 1500.0]

    with pytest.raises(AlreadyRegistered):
        await ladder.register(GUILD, ALICE, "alice")

    await ladder.register(GUILD, CAROL, "carol")
    assert await ranking(ladder) == [ALICE, BOB, CAROL]


async def test_guilds_are_isolated(services: Services) -> None:
    await register_all(services.ladder, ALICE)
    assert await services.ladder.leaderboard(GUILD + 1) == []


async def test_open_week_pairs_by_rating_and_once_each(
    services: Services, uow_factory: UnitOfWorkFactory
) -> None:
    ladder = services.ladder
    with pytest.raises(NotEnoughPlayers):
        await ladder.open_week(GUILD)

    await register_all(ladder, ALICE, BOB, CAROL, DAVE)
    await set_rating(uow_factory, ALICE, 1900)
    await set_rating(uow_factory, BOB, 1850)
    await set_rating(uow_factory, CAROL, 1500)
    await set_rating(uow_factory, DAVE, 1450)

    overview = await ladder.open_week(GUILD)

    assert overview.week.number == 1
    pairings = {frozenset((s.player1.discord_id, s.player2.discord_id)) for s in overview.matches}
    assert pairings == {frozenset((ALICE, BOB)), frozenset((CAROL, DAVE))}
    assert len(overview.matches) == 2
    assert overview.bye is None
    with pytest.raises(WeekAlreadyOpen):
        await ladder.open_week(GUILD)


async def test_open_week_avoids_recent_rematches(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB, CAROL, DAVE)
    first = await ladder.open_week(GUILD)
    first_pairs = {frozenset((s.player1.discord_id, s.player2.discord_id)) for s in first.matches}
    for summary in first.matches:
        await ladder.set_result(GUILD, summary.match.pk, 2, 0)
    await ladder.close_week(GUILD)

    second = await ladder.open_week(GUILD)
    second_pairs = {frozenset((s.player1.discord_id, s.player2.discord_id)) for s in second.matches}

    assert first_pairs.isdisjoint(second_pairs)
    assert len(second.matches) == 2


async def test_close_week_updates_ratings_and_placement(
    services: Services, uow_factory: UnitOfWorkFactory
) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)

    closing = await play_week(ladder, winner=BOB)

    bob = await get_player(uow_factory, BOB)
    alice = await get_player(uow_factory, ALICE)
    assert bob.rating > 1500 > alice.rating
    assert (bob.rated_matches, alice.rated_matches) == (1, 1)
    assert bob.rank == 1 and alice.rank == 2
    assert closing.ranking[0].player.discord_id == BOB
    assert closing.ranking[0].rating_delta > 0
    assert closing.ranking[1].rating_delta < 0
    assert all(change.provisional for change in closing.ranking)

    for _ in range(3):
        await play_week(ladder, winner=BOB)

    bob = await get_player(uow_factory, BOB)
    alice = await get_player(uow_factory, ALICE)
    assert (bob.rated_matches, alice.rated_matches) == (4, 4)
    entries = await ladder.leaderboard(GUILD)
    assert all(not entry.provisional for entry in entries)
    assert [entry.player.discord_id for entry in entries] == [BOB, ALICE]


async def test_ranked_players_appear_before_provisional_ones(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)
    for _ in range(4):
        await play_week(ladder, winner=BOB)

    await ladder.register(GUILD, CAROL, "carol")
    entries = await ladder.leaderboard(GUILD)

    assert [entry.player.discord_id for entry in entries] == [BOB, ALICE, CAROL]
    assert [entry.provisional for entry in entries] == [False, False, True]
    assert entries[-1].player.rank == 3


async def test_register_mid_week_pairs_available_players(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)
    await ladder.open_week(GUILD)

    carol = await ladder.register(GUILD, CAROL, "carol")
    assert carol.match is None  # Alice et Bob sont déjà appariés

    dave = await ladder.register(GUILD, DAVE, "dave")
    assert dave.match is not None
    assert dave.match.opponent_of(DAVE).discord_id == CAROL

    overview = await ladder.week_overview(GUILD)
    assert overview is not None
    assert len(overview.matches) == 2
    matched = [
        pid
        for summary in overview.matches
        for pid in (summary.player1.discord_id, summary.player2.discord_id)
    ]
    assert sorted(matched) == [ALICE, BOB, CAROL, DAVE]


async def test_remove_player_voids_and_re_pairs(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB, CAROL)
    overview = await ladder.open_week(GUILD)
    assert overview.bye is not None
    assert overview.bye.discord_id == CAROL

    await ladder.remove_player(GUILD, ALICE)

    assert await ranking(ladder) == [BOB, CAROL]
    assert (await ladder.get_match(GUILD, overview.matches[0].match.pk)).match.status is (
        MatchStatus.VOIDED
    )
    summary = await ladder.current_match(GUILD, BOB)
    assert summary.opponent_of(BOB).discord_id == CAROL
    with pytest.raises(NotRegistered):
        await ladder.current_match(GUILD, ALICE)

    registration = await ladder.register(GUILD, ALICE, "alice")
    assert registration.reactivated
    assert await ranking(ladder) == [BOB, CAROL, ALICE]


async def test_close_week_confirms_pending_reports_and_voids_unplayed(
    services: Services,
) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB, CAROL, DAVE)
    overview = await ladder.open_week(GUILD)
    reported, unplayed = (s.match.pk for s in overview.matches)
    await ladder.report_result(GUILD, reported, BOB, 2, 0)  # jamais confirmé par Alice

    closing = await ladder.close_week(GUILD)

    assert (closing.confirmed, closing.voided) == (1, 1)
    assert (await ladder.get_match(GUILD, reported)).match.status is MatchStatus.CONFIRMED
    assert (await ladder.get_match(GUILD, unplayed)).match.status is MatchStatus.VOIDED
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


async def test_admin_can_change_a_confirmed_result_before_closing(services: Services) -> None:
    """Tant que la semaine est ouverte, un admin peut remplacer un résultat validé."""
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)
    match_id = (await ladder.open_week(GUILD)).matches[0].match.pk
    await ladder.set_result(GUILD, match_id, 2, 0)  # Alice gagne

    summary = await ladder.set_result(GUILD, match_id, 0, 2)  # finalement Bob
    assert summary.winner is not None
    assert summary.winner.discord_id == BOB

    await ladder.close_week(GUILD)
    assert await ranking(ladder) == [BOB, ALICE]


async def test_correcting_a_result_after_closing_is_refused(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)
    match_id = (await ladder.open_week(GUILD)).matches[0].match.pk
    await ladder.set_result(GUILD, match_id, 2, 0)
    await ladder.close_week(GUILD)

    with pytest.raises(InvalidMatchState, match="clôturée"):
        await ladder.set_result(GUILD, match_id, 0, 2)


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


async def test_closed_week_rejects_new_results(services: Services) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)
    match_id = (await ladder.open_week(GUILD)).matches[0].match.pk
    await ladder.close_week(GUILD)

    with pytest.raises(InvalidMatchState, match="clôturée"):
        await ladder.report_result(GUILD, match_id, ALICE, 2, 0)


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
    assert bob.provisional and alice.provisional
    assert bob.rating > 1500 > alice.rating


async def test_bye_player_gets_no_rating_change(
    services: Services, uow_factory: UnitOfWorkFactory
) -> None:
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB, CAROL)
    overview = await ladder.open_week(GUILD)
    assert overview.bye is not None
    bye_id = overview.bye.discord_id

    for summary in overview.matches:
        await ladder.set_result(GUILD, summary.match.pk, 2, 0)
    await ladder.close_week(GUILD)

    bye = await get_player(uow_factory, bye_id)
    assert bye.rating == 1500.0
    assert (bye.rated_matches, bye.inactive_weeks, bye.byes) == (0, 1, 1)


async def test_stale_confirmation_is_rejected(services: Services) -> None:
    """Un bouton créé pour l'ancien score ne doit pas valider le nouveau."""
    ladder = services.ladder
    await register_all(ladder, ALICE, BOB)
    match_id = (await ladder.open_week(GUILD)).matches[0].match.pk

    await ladder.report_result(GUILD, match_id, ALICE, 2, 0)
    old = ReportFingerprint.of((await ladder.get_match(GUILD, match_id)).match)
    assert old is not None

    # Alice corrige son score après l'envoi du message contenant les boutons.
    await ladder.report_result(GUILD, match_id, ALICE, 2, 1)

    with pytest.raises(StaleReport):
        await ladder.confirm_result(GUILD, match_id, BOB, expected=old)

    current = ReportFingerprint.of((await ladder.get_match(GUILD, match_id)).match)
    assert current is not None
    summary = await ladder.confirm_result(GUILD, match_id, BOB, expected=current)
    assert summary.match.status is MatchStatus.CONFIRMED
