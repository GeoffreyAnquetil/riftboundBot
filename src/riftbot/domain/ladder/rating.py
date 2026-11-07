"""Classement par compétence : Glicko-2.

Le ladder se joue une fois par semaine, ce qui correspond exactement au modèle de Glicko-2 :
un « rating period » = une semaine, avec au plus un match par joueur. On met donc à jour les
notes à la clôture de la semaine, pour que tous les matchs d'une même semaine partent des mêmes
notes de départ.

Conventions :
- ``mu`` est la note (1500 au départ) ;
- ``deviation`` (RD) mesure l'incertitude (350 au départ, ~50 pour un joueur installé) ;
- ``volatility`` (sigma) mesure la variabilité de la performance.

Le module est du Python pur : aucun accès à Discord, à la base ou au temps réel. Les tests
peuvent donc vérifier les formules sans infrastructure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import exp, log, pi, sqrt

SCALE = 173.7178
DEFAULT_RATING = 1500.0
DEFAULT_DEVIATION = 350.0
DEFAULT_VOLATILITY = 0.06
DEFAULT_TAU = 0.5

# Bornes de l'incertitude. ``RD_RESET_PERIODS`` est le nombre de semaines sans jouer
# nécessaire pour qu'un joueur très sûr (RD=50) redevienne aussi incertain qu'un débutant
# (RD=350). C'est une convention lisible pour les joueurs : après un an d'absence, on ne sait
# plus grand-chose de leur niveau.
MIN_DEVIATION = 50.0
MAX_DEVIATION = 350.0
RD_RESET_PERIODS = 52

_EPSILON = 1e-6


@dataclass(frozen=True)
class Rating:
    """Note Glicko-2 d'un joueur."""

    mu: float = DEFAULT_RATING
    deviation: float = DEFAULT_DEVIATION
    volatility: float = DEFAULT_VOLATILITY

    @property
    def conservative(self) -> float:
        """Note prudente (μ − RD), utile pour départager des joueurs peu testés."""
        return self.mu - self.deviation

    def inflate(self, periods: int = 1, *, reset_periods: int = RD_RESET_PERIODS) -> Rating:
        """Augmente l'incertitude après ``periods`` semaines sans match.

        La note ne bouge pas : seule la confiance qu'on lui accorde diminue. Un joueur qui
        reprend après une absence peut ainsi se recalibrer plus vite.
        """
        if periods <= 0:
            return self
        if reset_periods <= 0:
            raise ValueError("reset_periods doit être positif")
        growth = sqrt((MAX_DEVIATION**2 - MIN_DEVIATION**2) / reset_periods)
        deviation = min(sqrt(self.deviation**2 + growth**2 * periods), MAX_DEVIATION)
        return Rating(self.mu, deviation, self.volatility)


def _g(deviation: float) -> float:
    phi = deviation / SCALE
    return 1 / sqrt(1 + 3 * phi * phi / (pi * pi))


def _expected_score(rating: float, opponent: float, opponent_deviation: float) -> float:
    """Probabilité que ``rating`` batte ``opponent`` (échelle Glicko-2)."""
    return 1 / (1 + exp(-_g(opponent_deviation) * (rating - opponent) / SCALE))


def _f(x: float, delta: float, phi: float, v: float, a: float, tau: float) -> float:
    ex = exp(x)
    numerator = ex * (delta * delta - phi * phi - v - ex)
    denominator = 2 * (phi * phi + v + ex) ** 2
    return numerator / denominator - (x - a) / (tau * tau)


def _solve_volatility(delta: float, phi: float, v: float, sigma: float, tau: float) -> float:
    """Trouve la nouvelle volatilité par bissection.

    Glicko-2 définit la volatilité comme la racine de :func:`_f`. La méthode de Newton
    proposée dans l'article peut dégénérer quand les deux bornes ont la même valeur de
    fonction ; une bissection encadrée est plus simple et plus robuste, pour un coût
    négligeable (un appel par joueur et par semaine).
    """
    a = log(sigma * sigma)

    if delta * delta > phi * phi + v:
        lower = log(delta * delta - phi * phi - v)
    else:
        k = 1
        while _f(a - k * tau, delta, phi, v, a, tau) < 0:
            k += 1
            if k > 1000:
                break
        lower = a - k * tau

    upper = a
    # Encadre la racine : f(lower) >= 0 et f(upper) <= 0.
    step = max(tau, 1.0)
    while _f(lower, delta, phi, v, a, tau) < 0:
        lower -= step
        step *= 2
    step = max(tau, 1.0)
    while _f(upper, delta, phi, v, a, tau) > 0:
        upper += step
        step *= 2

    for _ in range(100):
        middle = (lower + upper) / 2
        if abs(upper - lower) <= _EPSILON:
            break
        if _f(middle, delta, phi, v, a, tau) > 0:
            lower = middle
        else:
            upper = middle
    return exp((lower + upper) / 2 / 2)


def update(
    rating: Rating,
    opponents: Sequence[tuple[Rating, float]],
    *,
    tau: float = DEFAULT_TAU,
) -> Rating:
    """Met à jour une note à partir de plusieurs résultats.

    ``opponents`` contient des couples ``(note de l'adversaire, score)`` où le score vaut
    1.0 pour une victoire, 0.5 pour un nul et 0.0 pour une défaite. Tous les adversaires
    doivent être exprimés avec les notes d'*avant* la période : l'appelant ne doit pas
    enchaîner les mises à jour d'un même match.
    """
    if not opponents:
        return rating
    if tau <= 0:
        raise ValueError("tau doit être positif")
    for _, score in opponents:
        if not 0.0 <= score <= 1.0:
            raise ValueError("un score doit être compris entre 0 et 1")

    mu = (rating.mu - DEFAULT_RATING) / SCALE
    phi = rating.deviation / SCALE

    v = 0.0
    delta_sum = 0.0
    for opponent, score in opponents:
        opponent_mu = (opponent.mu - DEFAULT_RATING) / SCALE
        opponent_phi = opponent.deviation / SCALE
        g = 1 / sqrt(1 + 3 * opponent_phi * opponent_phi / (pi * pi))
        expected = 1 / (1 + exp(-g * (mu - opponent_mu)))
        v += g * g * expected * (1 - expected)
        delta_sum += g * (score - expected)

    if v <= 0:
        return rating
    v = 1 / v
    delta = v * delta_sum

    new_sigma = _solve_volatility(delta, phi, v, rating.volatility, tau)
    phi_star = sqrt(phi * phi + new_sigma * new_sigma)
    new_phi = 1 / sqrt(1 / (phi_star * phi_star) + 1 / v)
    new_mu = mu + new_phi * new_phi * delta_sum

    return Rating(
        mu=new_mu * SCALE + DEFAULT_RATING,
        deviation=new_phi * SCALE,
        volatility=new_sigma,
    )


def update_match(
    first: Rating,
    second: Rating,
    first_score: float,
    *,
    tau: float = DEFAULT_TAU,
) -> tuple[Rating, Rating]:
    """Met à jour les deux joueurs d'un match, à partir des notes d'avant-match.

    ``first_score`` est le score du premier joueur (1.0, 0.5 ou 0.0).
    """
    if not 0.0 <= first_score <= 1.0:
        raise ValueError("le score doit être compris entre 0 et 1")
    return (
        update(first, [(second, first_score)], tau=tau),
        update(second, [(first, 1.0 - first_score)], tau=tau),
    )


def expected_score(first: Rating, second: Rating) -> float:
    """Probabilité que ``first`` gagne contre ``second``, pour l'affichage et les tests."""
    return _expected_score(first.mu, second.mu, second.deviation)
