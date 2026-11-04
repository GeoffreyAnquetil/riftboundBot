"""Création du moteur SQL et migrations."""

from __future__ import annotations

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


def create_engine(url: str) -> AsyncEngine:
    """Moteur async. Avec SQLite, crée le dossier du fichier et active les clés étrangères."""
    engine = create_async_engine(url)
    if ensure_sqlite_dir(url):
        event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
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


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
