"""Appariement hebdomadaire : un match par joueur, sans revanche récente.

Le problème est un couplage parfait de coût minimal sur un graphe complet pondéré par l'écart
de note. On ajoute des contraintes de revanche, traitées par paliers :

1. aucun adversaire affronté depuis ``preferred_weeks`` semaines ;
2. aucun adversaire affronté depuis ``hard_weeks`` semaines ;
3. n'importe qui (seulement si les paliers précédents n'ont aucune solution).

Un palier n'est accepté que s'il permet de caser tout le monde (ou tout le monde sauf un
exempt). À l'intérieur d'un palier, on minimise la somme des écarts de note : les matchs
restent équilibrés.

Le module est du Python pur, sans dépendance : les tests peuvent vérifier les appariements
sans base ni Discord.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

# Au-delà de ce nombre de joueurs, la programmation dynamique exacte devient coûteuse :
# on bascule sur une heuristique gloutonne améliorée par des échanges 2-à-2.
EXACT_MATCHING_LIMIT = 16

PairKey = tuple[int, int]
History = Mapping[PairKey, int]
Allowed = Callable[[int, int], bool]


@dataclass(frozen=True)
class PairingCandidate:
    """Un joueur disponible pour la semaine."""

    player_id: int
    rating: float
    rating_deviation: float = 350.0
    byes: int = 0
    rank: int = 0


@dataclass(frozen=True)
class Pairing:
    """Deux joueurs appariés. ``higher`` est le mieux noté au moment du tirage."""

    higher: int
    lower: int
    rating_gap: float
    last_met_weeks: int | None = None


@dataclass(frozen=True)
class PairingPlan:
    pairings: tuple[Pairing, ...]
    bye: int | None
    tier: int
    warnings: tuple[str, ...] = ()


def pair_key(first: int, second: int) -> PairKey:
    return (first, second) if first < second else (second, first)


def pair_week(
    players: Sequence[PairingCandidate],
    history: History,
    *,
    preferred_weeks: int = 4,
    hard_weeks: int = 2,
) -> PairingPlan:
    """Construit les matchs de la semaine.

    ``history`` associe chaque paire déjà rencontrée au nombre de semaines écoulées depuis
    leur dernier match confirmé. Une paire absente n'a jamais joué.
    """
    if preferred_weeks < hard_weeks:
        raise ValueError("preferred_weeks doit être supérieur ou égal à hard_weeks")
    if len({p.player_id for p in players}) != len(players):
        raise ValueError("les identifiants de joueurs doivent être uniques")

    candidates = sorted(players, key=lambda p: (-p.rating, p.player_id))
    for tier in range(3):
        plan = _plan_for_tier(
            candidates,
            history,
            tier,
            preferred_weeks=preferred_weeks,
            hard_weeks=hard_weeks,
        )
        if plan is not None:
            return plan

    # Le graphe complet (palier 2) possède toujours un couplage parfait : on n'arrive ici
    # que si un appelant a fourni des données incohérentes.
    raise RuntimeError("aucun appariement possible")


def _plan_for_tier(
    candidates: Sequence[PairingCandidate],
    history: History,
    tier: int,
    *,
    preferred_weeks: int,
    hard_weeks: int,
) -> PairingPlan | None:
    def allowed(first: int, second: int) -> bool:
        return _allowed(
            candidates[first],
            candidates[second],
            history,
            tier,
            preferred_weeks,
            hard_weeks,
        )

    if len(candidates) % 2 == 0:
        matching = _best_matching(candidates, allowed)
        if matching is None:
            return None
        pairs, _ = matching
        return _plan(candidates, pairs, history, tier)

    best: tuple[tuple[int, int, float, float, int], tuple[tuple[int, int], ...], int] | None = None
    for index, candidate in enumerate(candidates):
        rest = [c for position, c in enumerate(candidates) if position != index]

        matching = _best_matching(
            rest,
            _allowed_for(rest, history, tier, preferred_weeks, hard_weeks),
        )
        if matching is None:
            continue
        pairs, cost = matching
        # L'exempt va d'abord au joueur qui en a le moins eu, puis au moins bien classé
        # (rang le plus grand), puis au moins bien noté. Le coût ne sert qu'à départager :
        # on ne veut pas donner l'exempt au leader simplement parce que cela rend les
        # autres matchs plus serrés.
        key = (candidate.byes, -candidate.rank, candidate.rating, cost, candidate.player_id)
        if best is None or key < best[0]:
            best = (key, pairs, candidate.player_id)

    if best is None:
        return None
    _, rest_pairs, bye = best
    # Les indices de ``rest_pairs`` portent sur ``rest`` : on les retraduit en indices
    # de la liste complète pour construire le plan.
    original = [i for i in range(len(candidates)) if candidates[i].player_id != bye]
    pairs = tuple((original[first], original[second]) for first, second in rest_pairs)
    return _plan(candidates, pairs, history, tier, bye=bye)


def _plan(
    candidates: Sequence[PairingCandidate],
    pairs: Sequence[tuple[int, int]],
    history: History,
    tier: int,
    *,
    bye: int | None = None,
) -> PairingPlan:
    pairings = tuple(
        Pairing(
            higher=candidates[first].player_id,
            lower=candidates[second].player_id,
            rating_gap=abs(candidates[first].rating - candidates[second].rating),
            last_met_weeks=history.get(
                pair_key(candidates[first].player_id, candidates[second].player_id)
            ),
        )
        for first, second in pairs
    )
    warnings: tuple[str, ...] = ()
    if tier == 1:
        warnings = ("Fenêtre de revanche élargie : aucun appariement ne respectait la préférence.",)
    elif tier >= 2:
        warnings = ("Revanche inévitable : aucun appariement sans adversaire récent.",)
    return PairingPlan(pairings=pairings, bye=bye, tier=tier, warnings=warnings)


def _allowed_for(
    candidates: Sequence[PairingCandidate],
    history: History,
    tier: int,
    preferred_weeks: int,
    hard_weeks: int,
) -> Allowed:
    def allowed(first: int, second: int) -> bool:
        return _allowed(
            candidates[first],
            candidates[second],
            history,
            tier,
            preferred_weeks,
            hard_weeks,
        )

    return allowed


def _allowed(
    first: PairingCandidate,
    second: PairingCandidate,
    history: History,
    tier: int,
    preferred_weeks: int,
    hard_weeks: int,
) -> bool:
    if tier >= 2:
        return True
    weeks = history.get(pair_key(first.player_id, second.player_id))
    if weeks is None:
        return True
    limit = preferred_weeks if tier == 0 else hard_weeks
    return weeks > limit


def _best_matching(
    candidates: Sequence[PairingCandidate],
    allowed: Allowed,
) -> tuple[tuple[tuple[int, int], ...], float] | None:
    """Couplage parfait de coût minimal, ou ``None`` s'il n'existe pas."""
    if not candidates:
        return (), 0.0
    if len(candidates) % 2 != 0:
        return None
    if len(candidates) <= EXACT_MATCHING_LIMIT:
        return _exact_matching(candidates, allowed)
    return _greedy_matching(candidates, allowed)


def _exact_matching(
    candidates: Sequence[PairingCandidate],
    allowed: Allowed,
) -> tuple[tuple[tuple[int, int], ...], float] | None:
    count = len(candidates)
    memo: dict[int, tuple[float, tuple[tuple[int, int], ...]] | None] = {}

    def solve(mask: int) -> tuple[float, tuple[tuple[int, int], ...]] | None:
        if mask == 0:
            return 0.0, ()
        if mask in memo:
            return memo[mask]

        first = (mask & -mask).bit_length() - 1
        best: tuple[float, tuple[tuple[int, int], ...]] | None = None
        for second in range(first + 1, count):
            if not mask & (1 << second) or not allowed(first, second):
                continue
            rest = solve(mask ^ (1 << first) ^ (1 << second))
            if rest is None:
                continue
            cost = abs(candidates[first].rating - candidates[second].rating) + rest[0]
            if best is None or cost < best[0] - 1e-9:
                best = (cost, ((first, second), *rest[1]))
        memo[mask] = best
        return best

    result = solve((1 << count) - 1)
    if result is None:
        return None
    return result[1], result[0]


def _greedy_matching(
    candidates: Sequence[PairingCandidate],
    allowed: Allowed,
) -> tuple[tuple[tuple[int, int], ...], float] | None:
    """Heuristique pour les grands groupes : glouton puis échanges 2-à-2."""
    count = len(candidates)
    remaining = set(range(count))
    pairs: list[tuple[int, int]] = []

    while remaining:
        first = min(remaining)
        remaining.remove(first)
        candidates_for_second = [second for second in remaining if allowed(first, second)]
        if not candidates_for_second:
            return None
        second = min(
            candidates_for_second,
            key=lambda index: (
                abs(candidates[first].rating - candidates[index].rating),
                index,
            ),
        )
        remaining.remove(second)
        pairs.append((first, second))

    improved = True
    while improved:
        improved = False
        for left in range(len(pairs)):
            for right in range(left + 1, len(pairs)):
                a, b = pairs[left]
                c, d = pairs[right]
                current = _pair_cost(candidates, a, b) + _pair_cost(candidates, c, d)
                alternatives = (
                    ((a, c), (b, d)),
                    ((a, d), (b, c)),
                )
                for new_left, new_right in alternatives:
                    if not allowed(*new_left) or not allowed(*new_right):
                        continue
                    cost = _pair_cost(candidates, *new_left) + _pair_cost(candidates, *new_right)
                    if cost < current - 1e-9:
                        pairs[left], pairs[right] = new_left, new_right
                        improved = True
                        break
                if improved:
                    break
            if improved:
                break

    return tuple(pairs), sum(_pair_cost(candidates, a, b) for a, b in pairs)


def _pair_cost(candidates: Sequence[PairingCandidate], first: int, second: int) -> float:
    return abs(candidates[first].rating - candidates[second].rating)
