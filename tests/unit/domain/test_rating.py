from __future__ import annotations

import pytest

from riftbot.domain.ladder.rating import (
    DEFAULT_DEVIATION,
    DEFAULT_RATING,
    Rating,
    expected_score,
    update,
    update_match,
)


def test_new_player_starts_at_the_default_rating() -> None:
    rating = Rating()
    assert (rating.mu, rating.deviation, rating.volatility) == (
        DEFAULT_RATING,
        DEFAULT_DEVIATION,
        0.06,
    )


def test_a_win_raises_the_rating_and_reduces_uncertainty() -> None:
    player = Rating(1500, 200, 0.06)
    opponent = Rating(1400, 30, 0.06)

    updated = update(player, [(opponent, 1.0)])

    assert updated.mu == pytest.approx(1563.5642, abs=0.001)
    assert updated.deviation < player.deviation
    assert updated.volatility == pytest.approx(0.059996, abs=1e-5)


def test_a_loss_lowers_the_rating() -> None:
    player = Rating(1500, 200, 0.06)
    opponent = Rating(1400, 30, 0.06)

    updated = update(player, [(opponent, 0.0)])

    assert updated.mu < player.mu


def test_both_players_are_updated_from_pre_match_values() -> None:
    first = Rating(1500, 200, 0.06)
    second = Rating(1400, 30, 0.06)

    new_first, new_second = update_match(first, second, 1.0)

    assert new_first.mu > first.mu
    assert new_second.mu < second.mu
    # Le perdant ne doit pas être calculé à partir de la note déjà mise à jour du vainqueur.
    assert new_second.mu == pytest.approx(update(second, [(first, 0.0)]).mu)


def test_multiple_opponents_match_the_reference_period() -> None:
    """Valeurs de référence de l'article Glicko-2 pour une période à trois matchs."""
    player = Rating(1500, 200, 0.06)
    opponents = [
        (Rating(1400, 30, 0.06), 1.0),
        (Rating(1550, 100, 0.06), 0.0),
        (Rating(1700, 300, 0.06), 0.5),
    ]

    updated = update(player, opponents)

    assert updated.deviation == pytest.approx(151.51652298866463, abs=1e-9)
    assert updated.volatility == pytest.approx(0.0599956943, abs=1e-9)


def test_upset_is_worth_more_than_an_expected_win() -> None:
    player = Rating(1500, 200, 0.06)
    stronger = Rating(1800, 60, 0.06)
    weaker = Rating(1200, 60, 0.06)

    upset = update(player, [(stronger, 1.0)]).mu - player.mu
    expected_win = update(player, [(weaker, 1.0)]).mu - player.mu

    assert upset > expected_win


def test_expected_score_favours_the_stronger_player() -> None:
    stronger = Rating(1800, 60, 0.06)
    weaker = Rating(1500, 60, 0.06)

    assert expected_score(stronger, weaker) > 0.5
    assert expected_score(weaker, stronger) < 0.5
    assert expected_score(weaker, stronger) == pytest.approx(1 - expected_score(stronger, weaker))


def test_inactivity_inflates_uncertainty_without_changing_the_rating() -> None:
    rating = Rating(1900, 50, 0.06)

    inflated = rating.inflate(4)

    assert inflated.mu == rating.mu
    assert inflated.deviation > rating.deviation
    assert inflated.deviation <= 350


def test_inactivity_caps_at_the_initial_uncertainty() -> None:
    rating = Rating(1900, 50, 0.06)
    assert rating.inflate(10_000).deviation == pytest.approx(350)


def test_no_opponent_leaves_the_rating_unchanged() -> None:
    rating = Rating(1650, 90, 0.06)
    assert update(rating, []) == rating


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_score_must_be_between_zero_and_one(score: float) -> None:
    with pytest.raises(ValueError):
        update(Rating(), [(Rating(), score)])
