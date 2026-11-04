import pytest

from riftbot.domain.ladder.rules import AdjacentPairsRule, Outcome, Pairing

rule = AdjacentPairsRule()


def test_pairs_adjacent_players() -> None:
    assert rule.pair([10, 20, 30, 40], week_number=1) == [Pairing(10, 20), Pairing(30, 40)]


def test_last_player_is_exempt_with_odd_count() -> None:
    assert rule.pair([10, 20, 30], week_number=1) == [Pairing(10, 20)]


def test_lower_seed_winning_takes_the_better_spot() -> None:
    outcomes = [Outcome(10, 20, winner=20), Outcome(30, 40, winner=30)]
    assert rule.apply([10, 20, 30, 40], outcomes) == [20, 10, 30, 40]


def test_unplayed_match_changes_nothing() -> None:
    assert rule.apply([10, 20], [Outcome(10, 20, winner=None)]) == [10, 20]


def test_removed_player_is_ignored() -> None:
    assert rule.apply([10, 30], [Outcome(10, 20, winner=20)]) == [10, 30]


def test_winner_must_be_one_of_the_players() -> None:
    with pytest.raises(ValueError):
        Outcome(10, 20, winner=99)
