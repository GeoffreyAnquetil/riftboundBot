from __future__ import annotations

from riftbot.domain.ladder.pairing import PairingCandidate, PairingPlan, pair_key, pair_week


def candidate(player_id: int, rating: float, byes: int = 0, rank: int = 0) -> PairingCandidate:
    return PairingCandidate(player_id=player_id, rating=rating, byes=byes, rank=rank)


def pairs(plan: PairingPlan) -> set[tuple[int, int]]:
    return {pair_key(pairing.higher, pairing.lower) for pairing in plan.pairings}


def test_pairs_the_closest_ratings() -> None:
    plan = pair_week(
        [
            candidate(1, 1900),
            candidate(2, 1800),
            candidate(3, 1500),
            candidate(4, 1490),
        ],
        {},
    )

    assert pairs(plan) == {pair_key(1, 2), pair_key(3, 4)}


def test_avoids_recent_opponents_by_swapping_partners() -> None:
    history = {pair_key(1, 2): 1, pair_key(3, 4): 1}

    plan = pair_week(
        [candidate(1, 1500), candidate(2, 1500), candidate(3, 1500), candidate(4, 1500)],
        history,
        preferred_weeks=4,
        hard_weeks=2,
    )

    assert plan.tier == 0
    assert pairs(plan) == {pair_key(1, 3), pair_key(2, 4)}


def test_prefers_the_strictest_possible_tier() -> None:
    history = {
        pair_key(1, 2): 1,
        pair_key(3, 4): 1,
        pair_key(1, 4): 2,
        pair_key(2, 3): 2,
        pair_key(1, 3): 3,
        pair_key(2, 4): 3,
    }

    plan = pair_week(
        [candidate(1, 1500), candidate(2, 1500), candidate(3, 1500), candidate(4, 1500)],
        history,
        preferred_weeks=4,
        hard_weeks=2,
    )

    # Aucun appariement n'évite les 4 dernières semaines, mais les paires 1-3 et 2-4
    # (rencontrées il y a 3 semaines) restent autorisées par le palier minimal.
    assert plan.tier == 1
    assert pairs(plan) == {pair_key(1, 3), pair_key(2, 4)}


def test_falls_back_to_a_rematch_when_unavoidable() -> None:
    plan = pair_week(
        [candidate(1, 1500), candidate(2, 1500)],
        {pair_key(1, 2): 1},
        preferred_weeks=4,
        hard_weeks=2,
    )

    assert plan.tier == 2
    assert pairs(plan) == {pair_key(1, 2)}


def test_odd_group_gets_a_bye_for_the_lowest_rated_player() -> None:
    plan = pair_week(
        [candidate(1, 1900), candidate(2, 1800), candidate(3, 1500)],
        {},
    )

    assert plan.bye == 3
    assert pairs(plan) == {pair_key(1, 2)}


def test_bye_does_not_go_to_the_leader_when_the_others_are_close() -> None:
    plan = pair_week(
        [
            candidate(1, 1900, rank=1),
            candidate(2, 1500, rank=2),
            candidate(3, 1490, rank=3),
        ],
        {},
    )

    assert plan.bye == 3
    assert pairs(plan) == {pair_key(1, 2)}


def test_byes_are_spread_between_players() -> None:
    plan = pair_week(
        [
            candidate(1, 1500, byes=0),
            candidate(2, 1500, byes=0),
            candidate(3, 1500, byes=1),
        ],
        {},
    )

    assert plan.bye == 1
    assert pairs(plan) == {pair_key(2, 3)}


def test_rejects_duplicate_players() -> None:
    try:
        pair_week([candidate(1, 1500), candidate(1, 1600)], {})
    except ValueError:
        return
    raise AssertionError("un doublon aurait dû être refusé")
