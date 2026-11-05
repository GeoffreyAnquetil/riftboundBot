"""Point d'entrée : ``uv run riftbot`` ou ``python -m riftbot``.

Codes de sortie utilisés par le superviseur (Docker ``on-failure``, systemd…) :

- ``0`` : arrêt propre (ou interruption clavier) ;
- ``1`` : erreur transitoire, un redémarrage borné peut suffire ;
- ``78`` : erreur de configuration/authentification permanente, inutile de
  réessayer en boucle.

Ces codes sont surtout utiles pour les logs et un éventuel superviseur. Docker
ne sait pas distinguer 1 et 78, d'où le garde-fou de démarrage ci-dessous, qui
refuse de contacter Discord après trop de tentatives rapprochées.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from datetime import UTC, datetime, timedelta

import discord

from riftbot.bootstrap import build_sql_services
from riftbot.config import Settings
from riftbot.discord_bot.bot import RiftBot
from riftbot.infrastructure.sql.database import upgrade_database
from riftbot.infrastructure.startup_guard import StartupGuard, StartupLoopDetected

logger = logging.getLogger("riftbot")

EXIT_OK = 0
EXIT_TRANSIENT = 1
# 78 = EX_CONFIG dans sysexits(3) : erreur de configuration permanente.
EXIT_CONFIG = 78


def classify_startup_error(error: BaseException) -> int:
    """Traduit une erreur de démarrage en code de sortie.

    Une erreur d'authentification ou de permission ne guérira pas en
    redémarrant : on la signale comme permanente. Les 429/5xx et les erreurs
    réseau sont transitoires et peuvent justifier une nouvelle tentative bornée.
    """
    if isinstance(error, discord.LoginFailure):
        return EXIT_CONFIG
    if isinstance(error, discord.HTTPException) and error.status in (401, 403):
        return EXIT_CONFIG
    return EXIT_TRANSIENT


async def run(settings: Settings, token: str) -> None:
    services, engine = build_sql_services(settings)
    bot = RiftBot(
        services,
        dev_guild_id=settings.dev_guild_id,
        dev_commands=settings.enable_dev_commands,
        sync_cache=settings.command_sync_cache,
        force_sync=settings.force_command_sync,
    )
    try:
        async with bot:
            await bot.start(token)
    finally:
        await engine.dispose()


def main() -> int:
    """Démarre le bot et renvoie un code de sortie adapté au superviseur."""
    try:
        settings = Settings()
    except Exception as exc:  # pydantic refuse une variable mal formée
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return EXIT_CONFIG

    level = logging.getLevelName(settings.log_level.upper())
    if not isinstance(level, int):
        print(f"LOG_LEVEL invalide : {settings.log_level}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        discord.utils.setup_logging(level=level)
    except Exception as exc:
        print(f"Impossible de configurer les logs : {exc}", file=sys.stderr)
        return EXIT_CONFIG

    discord.VoiceClient.warn_nacl = False  # le bot n'utilise pas la voix

    token = settings.discord_token
    secret = token.get_secret_value() if token is not None else ""
    if not secret:
        logger.error("DISCORD_TOKEN manquant : copie .env.example en .env et renseigne-le.")
        return EXIT_CONFIG

    # Dernier rempart avant toute connexion Discord : si le processus redémarre
    # en boucle (crash, config cassée, sync refusée…), on arrête de contacter
    # Discord au bout de quelques tentatives pour éviter un blocage IP.
    guard = StartupGuard(
        settings.startup_guard_file,
        max_attempts=settings.startup_max_attempts,
        window=timedelta(seconds=settings.startup_window_seconds),
    )
    try:
        guard.check_and_record(datetime.now(UTC))
    except StartupLoopDetected as exc:
        logger.error("%s", exc)
        return EXIT_CONFIG

    logger.info("Migrations de la base…")
    try:
        upgrade_database(settings.database_url)
    except Exception:
        # Une base verrouillée ou momentanément indisponible peut se rétablir ;
        # une erreur de permissions sera arrêtée par la limite de redémarrages.
        logger.exception("Échec des migrations de la base")
        return EXIT_TRANSIENT

    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run(settings, secret))
    except discord.LoginFailure:
        logger.error("Token Discord refusé : vérifie DISCORD_TOKEN dans .env.")
        return EXIT_CONFIG
    except discord.HTTPException as exc:
        code = classify_startup_error(exc)
        if code == EXIT_CONFIG:
            logger.error(
                "Discord a refusé le démarrage (HTTP %s) : vérifie le token, "
                "les scopes et l'ID du serveur de dev.",
                exc.status,
            )
        else:
            logger.warning(
                "Discord est temporairement indisponible (HTTP %s) ; "
                "le superviseur pourra réessayer avec un délai.",
                exc.status,
            )
        return code
    except Exception:
        logger.exception("Erreur fatale au démarrage")
        return EXIT_TRANSIENT

    return EXIT_OK


if __name__ == "__main__":
    # Console script : ``main`` renvoie désormais un code de sortie explicite.
    raise SystemExit(main())
