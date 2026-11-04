from datetime import UTC, datetime

import pytest

from riftbot.domain.errors import InvalidMatchState, InvalidScore, NotAllowed
from riftbot.domain.ladder.models import Match, MatchFormat, MatchStatus, Side

BO3 = MatchFormat(best_of=3)
NOW = datetime(2026, 11, 4, tzinfo=UTC)
P1, P2, OUTSIDER = 1, 2, 3


def new_match() -> Match:
    return Match(id=1, guild_id=1, week_id=1, player1_id=P1, player2_id=P2)


@pytest.mark.parametrize("score", [(2, 0), (2, 1), (0, 2), (1, 2)])
def test_bo3_valid_scores(score: tuple[int, int]) -> None:
    BO3.validate(*score)


@pytest.mark.parametrize("score", [(2, 2), (3, 0), (1, 0), (1, 1), (-1, 2), (0, 0)])
def test_bo3_invalid_scores(score: tuple[int, int]) -> None:
    with pytest.raises(InvalidScore):
        BO3.validate(*score)


def test_winning_scores() -> None:
    assert BO3.winning_scores() == [(2, 0), (2, 1)]
    assert MatchFormat(best_of=1).winning_scores() == [(1, 0)]


def test_best_of_must_be_odd() -> None:
    with pytest.raises(ValueError):
        MatchFormat(best_of=2)


def test_report_is_stored_from_player1_point_of_view() -> None:
    match = new_match()
    assert match.report(P2, 2, 1, BO3, NOW) is MatchStatus.REPORTED
    assert (match.score1, match.score2) == (1, 2)
    assert match.reported_by == P2


def test_opponent_confirms_report() -> None:
    match = new_match()
    match.report(P1, 2, 0, BO3, NOW)
    match.confirm(P2, NOW)
    assert match.status is MatchStatus.CONFIRMED
    assert match.winner_side is Side.ONE
    assert (match.winner_id, match.loser_id) == (P1, P2)


def test_reporter_cannot_confirm_own_report() -> None:
    match = new_match()
    match.report(P1, 2, 0, BO3, NOW)
    with pytest.raises(NotAllowed):
        match.confirm(P1, NOW)


def test_concordant_reports_confirm() -> None:
    match = new_match()
    match.report(P1, 2, 1, BO3, NOW)
    assert match.report(P2, 1, 2, BO3, NOW) is MatchStatus.CONFIRMED


def test_contradictory_reports_dispute() -> None:
    match = new_match()
    match.report(P1, 2, 1, BO3, NOW)
    assert match.report(P2, 2, 0, BO3, NOW) is MatchStatus.DISPUTED
    with pytest.raises(InvalidMatchState):
        match.report(P1, 2, 1, BO3, NOW)


def test_reporter_can_correct_before_confirmation() -> None:
    match = new_match()
    match.report(P1, 2, 1, BO3, NOW)
    match.report(P1, 0, 2, BO3, NOW)
    assert (match.score1, match.score2) == (0, 2)
    assert match.status is MatchStatus.REPORTED


def test_outsider_cannot_report() -> None:
    with pytest.raises(NotAllowed):
        new_match().report(OUTSIDER, 2, 0, BO3, NOW)


def test_cannot_report_a_final_match() -> None:
    match = new_match()
    match.resolve(2, 0, BO3, NOW)
    with pytest.raises(InvalidMatchState):
        match.report(P2, 2, 0, BO3, NOW)


def test_admin_resolves_dispute() -> None:
    match = new_match()
    match.report(P1, 2, 1, BO3, NOW)
    match.dispute(P2)
    match.resolve(0, 2, BO3, NOW)
    assert match.status is MatchStatus.CONFIRMED
    assert match.winner_id == P2


def test_void() -> None:
    match = new_match()
    match.void(NOW)
    assert match.status is MatchStatus.VOIDED
    assert match.winner_side is None
    with pytest.raises(InvalidMatchState):
        match.void(NOW)


def test_bets_close_once_a_result_is_reported() -> None:
    match = new_match()
    assert match.is_open_for_bets
    match.report(P1, 2, 0, BO3, NOW)
    assert not match.is_open_for_bets
