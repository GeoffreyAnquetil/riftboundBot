"""Création du moteur SQL et migrations."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

logger = logging.getLogger(__name__)


def create_engine(url: str) -> AsyncEngine:
    """Moteur async. Avec SQLite, crée le dossier du fichier et applique les PRAGMA."""
    engine = create_async_engine(url)
    if ensure_sqlite_dir(url):
        event.listen(engine.sync_engine, "connect", _configure_sqlite)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def upgrade_database(url: str) -> None:
    """Applique les migrations Alembic jusqu'à la dernière (``alembic upgrade head``).

    À appeler hors de toute boucle asyncio : l'environnement Alembic lance la sienne.
    """
    ensure_sqlite_dir(url)
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))  # échappement configparser
    command.upgrade(config, "head")


def ensure_sqlite_dir(url: str) -> bool:
    """Crée le dossier du fichier SQLite si besoin. Renvoie True si l'URL est SQLite."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return False
    if parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    return True


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    """Réglages de chaque connexion SQLite.

    - ``foreign_keys`` : SQLite ne les applique pas par défaut.
    - ``journal_mode=WAL`` : une lecture ne bloque plus une écriture, et inversement.
    - ``busy_timeout`` : une écriture patiente au lieu d'échouer immédiatement sur
      « database is locked » quand deux interactions écrivent en même temps.

    Le mode WAL est persistant dans le fichier ; le redemander est sans effet. S'il
    échoue (base verrouillée au démarrage, système de fichiers qui ne le supporte
    pas), on continue sans WAL : mieux vaut un bot qui démarre.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            logger.warning("Impossible d'activer le mode WAL de SQLite", exc_info=True)
    finally:
        cursor.close()
