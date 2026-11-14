"""Garde-fou contre les boucles de redémarrage.

Un crash au démarrage (token invalide, synchronisation des commandes refusée,
base inaccessible, dépendance manquante…) peut provoquer des dizaines de
tentatives de connexion à Discord. Discord et son anti-abus Cloudflare peuvent
alors bloquer l'IP du serveur, ce qui affecte aussi les autres bots hébergés
sur la même machine.

Ce module remplit deux rôles :

- il compte les démarrages dans une fenêtre glissante et refuse de contacter
  Discord lorsque la limite est dépassée ;
- il mémorise un **temps de pause** après un HTTP 429 renvoyé par Cloudflare.
  Un redémarrage pendant cette pause sort immédiatement, sans contacter
  Discord : c'est ce qui évite d'aggraver (ou de prolonger) un blocage d'IP.

Il est volontairement « fail-open » : si le fichier d'état est illisible ou non
inscriptible, on laisse le bot démarrer plutôt que de l'empêcher de fonctionner.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class StartupLoopDetected(RuntimeError):
    """Trop de démarrages rapprochés : on refuse de contacter Discord."""


class DiscordCooldownActive(StartupLoopDetected):
    """Un 429 Cloudflare a été observé récemment : on attend avant de retenter."""

    def __init__(self, remaining: timedelta) -> None:
        self.remaining = remaining
        super().__init__(
            "Discord a bloqué cette adresse IP (HTTP 429) récemment : "
            f"prochaine tentative possible dans {format_delay(remaining)}. "
            "Redémarrer plus tôt ne ferait qu'aggraver le blocage."
        )


def format_delay(delay: timedelta) -> str:
    """Durée lisible dans les logs (« 90 s », « 30 min », « 18 h 06 min »)."""
    seconds = max(0, round(delay.total_seconds()))
    if seconds < 90:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} min"
    hours, remaining = divmod(seconds, 3600)
    return f"{hours} h {remaining // 60:02d} min"


@dataclass(frozen=True)
class _GuardState:
    """Contenu du fichier d'état (le format liste de l'ancienne version reste lu)."""

    attempts: tuple[datetime, ...] = ()
    blocked_until: datetime | None = None


@dataclass(frozen=True)
class StartupGuard:
    """Compte les démarrages dans une fenêtre glissante et gère les pauses.

    ``max_attempts`` vaut 0 pour désactiver le compteur (déconseillé en
    production). ``window`` est la durée pendant laquelle les tentatives sont
    conservées. Les pauses, elles, sont pilotées par :meth:`start_cooldown`.
    """

    path: Path
    max_attempts: int = 5
    window: timedelta = timedelta(minutes=10)

    def check_and_record(self, now: datetime) -> None:
        """Enregistre une tentative et lève si elle doit être refusée.

        L'appel doit avoir lieu *avant* toute connexion Discord et avant les
        migrations : c'est ce qui évite qu'un échec permanent ne se transforme
        en flux de requêtes vers Discord.

        Le fichier n'est pas critique : une erreur d'écriture est journalisée et
        n'empêche pas le démarrage (fail-open).
        """
        current = _to_utc(now)
        state = self._read_state()

        # La pause Cloudflare s'applique même si le compteur est désactivé :
        # elle répond à un blocage réseau, pas au nombre de démarrages.
        if state.blocked_until is not None and current < state.blocked_until:
            raise DiscordCooldownActive(state.blocked_until - current)

        if not self._enabled:
            return

        # Une pause terminée est effacée sans être conservée.
        state = _GuardState(attempts=state.attempts)

        # On ne garde que les tentatives encore dans la fenêtre : un ancien
        # crash ne doit pas bloquer le bot indéfiniment.
        attempts = [attempt for attempt in state.attempts if attempt > current - self.window]
        attempts.append(current)

        # On écrit avant de lever : la tentative bloquée est elle aussi comptée,
        # sinon un redémarrage automatique pourrait repasser juste après.
        self._write_state(_GuardState(attempts=tuple(attempts)))

        if len(attempts) > self.max_attempts:
            raise StartupLoopDetected(
                f"{len(attempts)} démarrages en {self.window.total_seconds():.0f} s "
                f"(limite : {self.max_attempts}). Arrêt volontaire pour éviter un "
                "blocage Discord/Cloudflare. Corrige la cause, puis relance le bot "
                "manuellement ou attends la fin de la fenêtre."
            )

    def start_cooldown(self, now: datetime, duration: timedelta) -> None:
        """Interdit tout contact avec Discord pendant ``duration``.

        Utilisé après un HTTP 429 « Cloudflare » : les redémarrages suivants
        sortent immédiatement, sans requête vers Discord, jusqu'à la fin de la
        pause. Les tentatives déjà comptées sont conservées.
        """
        if duration <= timedelta(0):
            return

        current = _to_utc(now)
        state = self._read_state()
        attempts = tuple(attempt for attempt in state.attempts if attempt > current - self.window)
        self._write_state(_GuardState(attempts=attempts, blocked_until=current + duration))

    @property
    def _enabled(self) -> bool:
        return self.max_attempts > 0 and self.window > timedelta(0)

    def _read_state(self) -> _GuardState:
        """Lit l'état précédent ; un fichier corrompu est ignoré."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return _GuardState()

        # Ancien format (une simple liste de dates) : on le comprend encore pour
        # ne pas perdre le compteur après une mise à jour.
        if isinstance(raw, list):
            return _GuardState(attempts=_parse_attempts(raw), blocked_until=None)

        if not isinstance(raw, dict):
            logger.warning("État de démarrage invalide dans %s : ignoré", self.path)
            return _GuardState()

        attempts = raw.get("attempts", [])
        blocked_until = raw.get("blocked_until")
        return _GuardState(
            attempts=_parse_attempts(attempts) if isinstance(attempts, list) else (),
            blocked_until=_parse_datetime(blocked_until),
        )

    def _write_state(self, state: _GuardState) -> None:
        """Écrit l'état de façon atomique, sans faire échouer le démarrage."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.tmp")
            payload = json.dumps(
                {
                    "attempts": [attempt.isoformat() for attempt in state.attempts],
                    "blocked_until": (
                        state.blocked_until.isoformat() if state.blocked_until is not None else None
                    ),
                }
            )
            temporary.write_text(payload, encoding="utf-8")
            # replace() est atomique sur le même système de fichiers : un crash
            # au mauvais moment ne laisse pas un fichier à moitié écrit.
            temporary.replace(self.path)
        except OSError:
            # Fail-open : mieux vaut démarrer sans garde-fou que bloquer un bot
            # sain à cause d'un volume en lecture seule.
            logger.warning(
                "Impossible d'écrire l'état de démarrage dans %s (garde-fou ignoré)",
                self.path,
                exc_info=True,
            )


def _to_utc(value: datetime) -> datetime:
    """Normalise en UTC pour comparer des dates de provenances différentes."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _to_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _parse_attempts(values: list[object]) -> tuple[datetime, ...]:
    attempts: list[datetime] = []
    for value in values:
        parsed = _parse_datetime(value)
        if parsed is not None:
            attempts.append(parsed)
    return tuple(attempts)
