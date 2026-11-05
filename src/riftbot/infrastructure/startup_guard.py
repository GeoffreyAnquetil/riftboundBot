"""Garde-fou contre les boucles de redémarrage.

Un crash au démarrage (token invalide, synchronisation des commandes refusée,
base inaccessible, dépendance manquante…) peut provoquer des dizaines de
tentatives de connexion à Discord. Discord et son anti-abus Cloudflare peuvent
alors bloquer l'IP du serveur, ce qui affecte aussi les autres bots hébergés
sur la même machine.

Ce module compte les démarrages dans une fenêtre glissante et refuse de
contacter Discord lorsque la limite est dépassée. Il est volontairement
« fail-open » : si le fichier d'état est illisible ou non inscriptible, on
laisse le bot démarrer plutôt que de l'empêcher de fonctionner.
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


@dataclass(frozen=True)
class StartupGuard:
    """Compte les démarrages dans une fenêtre glissante.

    ``max_attempts`` vaut 0 pour désactiver le garde-fou (déconseillé en
    production). ``window`` est la durée pendant laquelle les tentatives sont
    conservées.
    """

    path: Path
    max_attempts: int = 5
    window: timedelta = timedelta(minutes=10)

    def check_and_record(self, now: datetime) -> None:
        """Enregistre une tentative et lève si la limite est dépassée.

        L'appel doit avoir lieu *avant* toute connexion Discord et avant les
        migrations : c'est ce qui évite qu'un échec permanent ne se transforme
        en flux de requêtes vers Discord.

        Le fichier n'est pas critique : une erreur d'écriture est journalisée et
        n'empêche pas le démarrage (fail-open).
        """
        if self.max_attempts <= 0 or self.window <= timedelta(0):
            return

        # On normalise en UTC pour que la comparaison reste correcte même si
        # l'horloge système ou le fichier d'état vient d'un autre fuseau.
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        current = now.astimezone(UTC)
        cutoff = current - self.window

        # On ne garde que les tentatives encore dans la fenêtre : un ancien
        # crash ne doit pas bloquer le bot indéfiniment.
        attempts = [attempt for attempt in self._read_attempts() if attempt > cutoff]
        attempts.append(current)

        # On écrit avant de lever : la tentative bloquée est elle aussi comptée,
        # sinon un redémarrage automatique pourrait repasser juste après.
        self._write_attempts(attempts)

        if len(attempts) > self.max_attempts:
            raise StartupLoopDetected(
                f"{len(attempts)} démarrages en {self.window.total_seconds():.0f} s "
                f"(limite : {self.max_attempts}). Arrêt volontaire pour éviter un "
                "blocage Discord/Cloudflare. Corrige la cause, puis relance le bot "
                "manuellement ou attends la fin de la fenêtre."
            )

    def _read_attempts(self) -> list[datetime]:
        """Lit les tentatives précédentes ; un fichier corrompu est ignoré."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []

        if not isinstance(raw, list):
            logger.warning("État de démarrage invalide dans %s : ignoré", self.path)
            return []

        attempts: list[datetime] = []
        for value in raw:
            if not isinstance(value, str):
                continue
            try:
                attempt = datetime.fromisoformat(value)
            except ValueError:
                continue
            # Un fichier écrit par une ancienne version pourrait contenir des
            # dates naïves : on les considère comme UTC.
            if attempt.tzinfo is None:
                attempt = attempt.replace(tzinfo=UTC)
            attempts.append(attempt.astimezone(UTC))
        return attempts

    def _write_attempts(self, attempts: list[datetime]) -> None:
        """Écrit l'état de façon atomique, sans faire échouer le démarrage."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.tmp")
            payload = json.dumps([attempt.isoformat() for attempt in attempts])
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
