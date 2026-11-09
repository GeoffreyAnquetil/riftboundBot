"""Point d'entrée du conteneur.

Le conteneur démarre en root uniquement pour corriger les droits du volume de
données, puis abandonne immédiatement les privilèges au profit de l'utilisateur
``bot`` (uid 1000) avant de lancer le bot.

Pourquoi : ``docker compose`` crée ``./data`` en root sur un hôte Linux au premier
lancement. Sans ce correctif, le bot (uid 1000) ne peut pas créer ``riftbot.db`` et
les migrations échouent en boucle. Si le conteneur est déjà lancé avec ``--user``,
le script ne fait qu'exécuter la commande demandée.
"""

from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path

DATA_DIR = Path(os.environ.get("RIFTBOT_DATA_DIR", "/app/data"))
BOT_USER = os.environ.get("RIFTBOT_USER", "bot")
FALLBACK_UID = 1000
FALLBACK_GID = 1000


def _bot_account() -> tuple[int, int, str | None]:
    """Identifiants et dossier personnel de l'utilisateur ``bot``."""
    try:
        entry = pwd.getpwnam(BOT_USER)
    except KeyError:
        return FALLBACK_UID, FALLBACK_GID, None
    return entry.pw_uid, entry.pw_gid, entry.pw_dir


def _fix_ownership(uid: int, gid: int) -> None:
    """Rend le volume de données accessible à l'utilisateur du bot."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path in (DATA_DIR, *DATA_DIR.rglob("*")):
        try:
            os.chown(path, uid, gid)
        except OSError as exc:
            # Certains systèmes de fichiers (montages réseau, Docker rootless)
            # refusent le chown : on continue, le contrôle ci-dessous tranchera.
            print(f"riftbot: chown impossible sur {path} : {exc}", file=sys.stderr)


def _writable_by(uid: int, gid: int) -> bool:
    """Vrai si ``uid`` peut créer un fichier dans le volume de données."""
    try:
        info = DATA_DIR.stat()
    except OSError:
        return False
    if info.st_uid == uid:
        return bool(info.st_mode & 0o200) and bool(info.st_mode & 0o100)
    if info.st_gid == gid:
        return bool(info.st_mode & 0o020) and bool(info.st_mode & 0o010)
    return bool(info.st_mode & 0o002) and bool(info.st_mode & 0o001)


def _drop_privileges(uid: int, gid: int, home: str | None) -> None:
    """Abandonne les privilèges root avant de lancer le bot."""
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    if home:
        os.environ["HOME"] = home


def main(argv: list[str]) -> None:
    if os.geteuid() != 0:
        os.execvp(argv[0], argv)

    uid, gid, home = _bot_account()
    _fix_ownership(uid, gid)
    if not _writable_by(uid, gid):
        print(
            f"riftbot: {DATA_DIR} n'est pas accessible en écriture pour l'uid {uid}.\n"
            "Sur l'hôte, corrige les droits avant de relancer :\n"
            f"  mkdir -p data && sudo chown -R {uid}:{gid} data",
            file=sys.stderr,
        )
        raise SystemExit(1)

    _drop_privileges(uid, gid, home)
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main(sys.argv[1:])
