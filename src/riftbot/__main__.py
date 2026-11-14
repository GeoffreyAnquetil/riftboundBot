"""Point d'entrée : ``uv run riftbot`` ou ``python -m riftbot``.

Codes de sortie utilisés par le superviseur (Docker ``on-failure``, systemd…) :

- ``0`` : arrêt propre (ou interruption clavier) ;
- ``1`` : erreur transitoire, un redémarrage borné peut suffire ;
- ``78`` : erreur de configuration/authentification permanente, inutile de
  réessayer en boucle.

Ces codes sont surtout utiles pour les logs et un éventuel superviseur. Docker
ne sait pas distinguer 1 et 78, d'où le garde-fou de démarrage ci-dessous, qui
refuse de contacter Discord après trop de tentatives rapprochées.

Un HTTP 429 sans en-tête ``Via`` n'est pas une limite de débit de l'API : c'est
une réponse de Cloudflare, qui bloque l'adresse IP de l'hébergeur. Redémarrer
immédiatement ne fait qu'allonger le blocage. Le bot enregistre donc une pause
(``LOGIN_COOLDOWN_SECONDS``) et les redémarrages suivants sortent sans contacter
Discord jusqu'à son expiration.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Protocol

import discord

from riftbot.bootstrap import Services, build_sql_services
from riftbot.config import Settings
from riftbot.discord_bot.bot import RiftBot
from riftbot.infrastructure.sql.database import upgrade_database
from riftbot.infrastructure.startup_guard import (
    DiscordCooldownActive,
    StartupGuard,
    StartupLoopDetected,
    format_delay,
)

logger = logging.getLogger("riftbot")

EXIT_OK = 0
EXIT_TRANSIENT = 1
# 78 = EX_CONFIG dans sysexits(3) : erreur de configuration permanente.
EXIT_CONFIG = 78


class BotSession(Protocol):
    """Ce dont le démarrage a besoin, pour pouvoir le tester sans réseau."""

    async def __aenter__(self) -> BotSession: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def start(self, token: str) -> None: ...


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


def is_cloudflare_block(error: BaseException) -> bool:
    """Vrai pour un 429 renvoyé par Cloudflare, qui signale un blocage d'IP.

    discord.py ne laisse échapper un 429 que dans ce cas précis : un 429 de
    l'API Discord est normalement absorbé (attente de ``retry_after``). Sans
    en-tête ``Via``, la réponse ne vient pas de l'API mais de Cloudflare.
    """
    if not isinstance(error, discord.HTTPException) or error.status != 429:
        return False
    headers = getattr(error.response, "headers", None) or {}
    return not headers.get("Via")


def retry_after_seconds(error: discord.HTTPException) -> float | None:
    """Durée demandée par Cloudflare dans ``Retry-After``, si elle est lisible.

    Cloudflare indique parfois explicitement combien de temps l'IP restera
    bloquée (observé : plus de 18 h). La respecter évite des dizaines de
    tentatives inutiles pendant le blocage.
    """
    headers = getattr(error.response, "headers", None) or {}
    raw = headers.get("Retry-After")
    if not isinstance(raw, str):
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def cloudflare_cooldown(
    error: discord.HTTPException, configured: float, maximum: float
) -> timedelta:
    """Pause à appliquer après un blocage : la plus longue des deux durées.

    On prend le maximum entre la valeur configurée et ``Retry-After`` (plafonné
    par ``maximum``) : si Cloudflare annonce 18 h, inutile de réessayer toutes
    les 30 min. ``configured`` à 0 n'annule pas une demande de Cloudflare.
    """
    requested = retry_after_seconds(error)
    delay = max(0.0, configured)
    if requested is not None:
        delay = max(delay, min(requested, max(0.0, maximum)))
    return timedelta(seconds=delay)


def is_retryable_startup_error(error: BaseException) -> bool:
    """Vrai si relancer la connexion dans le même processus a un sens.

    On ne réessaie pas un 429 Cloudflare : la pause est bien plus longue que
    quelques secondes, et insister ne ferait qu'aggraver le blocage.
    """
    if isinstance(error, discord.HTTPException):
        if is_cloudflare_block(error):
            return False
        return error.status == 429 or error.status >= 500
    return False


async def start_with_retries(
    factory: Callable[[], BotSession],
    token: str,
    *,
    attempts: int,
    initial_delay: float,
    max_delay: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Démarre le bot, en réessayant les erreurs transitoires avec un délai.

    Un nouvel objet bot est créé à chaque tentative : discord.py ne réutilise
    pas proprement une session HTTP après un échec de connexion.
    """
    attempts = max(1, attempts)
    delay = max(0.0, initial_delay)

    for attempt in range(1, attempts + 1):
        bot = factory()
        try:
            async with bot:
                await bot.start(token)
        except Exception as exc:
            if attempt >= attempts or not is_retryable_startup_error(exc):
                raise
            logger.warning(
                "Connexion Discord impossible (%s) ; nouvelle tentative dans %s (%d/%d)",
                describe_startup_error(exc),
                format_delay(timedelta(seconds=delay)),
                attempt + 1,
                attempts,
            )
            await sleep(delay)
            delay = min(max(0.0, max_delay), delay * 2)
        else:
            # ``start`` ne rend la main qu'à l'arrêt du bot : ne pas relancer.
            return


def describe_startup_error(error: BaseException) -> str:
    """Résumé d'une erreur de connexion pour les logs."""
    if isinstance(error, discord.HTTPException):
        return f"HTTP {error.status}"
    return type(error).__name__


def describe_http_error(error: discord.HTTPException) -> str:
    """Détails d'une réponse HTTP, pour distinguer les deux types de 429.

    Une limite de débit de l'API Discord renvoie du JSON avec
    ``X-RateLimit-Scope`` et ``retry_after`` : discord.py l'absorbe seul. Un
    blocage Cloudflare renvoie du HTML sans en-tête ``Via``. Ces détails
    permettent de trancher dans les logs sans deviner.
    """
    headers = getattr(error.response, "headers", None) or {}
    details = [f"Via={'oui' if headers.get('Via') else 'non'}"]
    scope = headers.get("X-RateLimit-Scope")
    if scope:
        details.append(f"scope={scope}")
    retry_after = headers.get("Retry-After")
    if retry_after:
        details.append(f"Retry-After={retry_after}")
    content_type = headers.get("Content-Type")
    if content_type:
        details.append(f"Content-Type={content_type}")
    body = " ".join((error.text or "").split())[:180]
    if body:
        details.append(f"corps={body!r}")
    return " · ".join(details)


def make_bot(settings: Settings, services: Services) -> RiftBot:
    """Construit le bot Discord à partir de la configuration."""
    return RiftBot(
        services,
        dev_guild_id=settings.dev_guild_id,
        dev_commands=settings.enable_dev_commands,
        enable_betting=settings.enable_betting,
        bot_channel_id=settings.bot_channel_id,
        sync_cache=settings.command_sync_cache,
        force_sync=settings.force_command_sync,
    )


async def run(settings: Settings, token: str) -> None:
    services, engine = build_sql_services(settings)
    try:
        await start_with_retries(
            lambda: make_bot(settings, services),
            token,
            attempts=settings.login_max_attempts,
            initial_delay=settings.login_retry_delay_seconds,
            max_delay=settings.login_max_retry_delay_seconds,
        )
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

    # Affiche les réglages effectifs : sans cela, une variable absente du .env (ou lue
    # depuis un autre dossier) ne se voit qu'en observant le comportement du bot.
    env_path = Path(".env")
    logger.info(
        "Config : .env %s dans %s · placement=%d · revanche=%d/%d · inactif=%d",
        "chargé" if env_path.is_file() else "absent",
        Path.cwd(),
        settings.placement_matches,
        settings.rematch_preferred_weeks,
        settings.rematch_hard_weeks,
        settings.inactive_after_weeks,
    )

    # Dernier rempart avant toute connexion Discord : si le processus redémarre
    # en boucle (crash, config cassée, sync refusée…) ou si Cloudflare vient de
    # bloquer l'IP, on arrête de contacter Discord pour éviter d'aggraver la
    # situation.
    guard = StartupGuard(
        settings.startup_guard_file,
        max_attempts=settings.startup_max_attempts,
        window=timedelta(seconds=settings.startup_window_seconds),
    )
    try:
        guard.check_and_record(datetime.now(UTC))
    except DiscordCooldownActive as exc:
        # Blocage temporaire : le superviseur peut redémarrer, mais le bot
        # sortira de nouveau sans contacter Discord jusqu'à la fin de la pause.
        logger.warning("%s", exc)
        return EXIT_TRANSIENT
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
                "les scopes et l'ID du serveur de dev. Détails : %s",
                exc.status,
                describe_http_error(exc),
            )
        elif is_cloudflare_block(exc):
            cooldown = cloudflare_cooldown(
                exc,
                configured=settings.login_cooldown_seconds,
                maximum=settings.login_max_cooldown_seconds,
            )
            guard.start_cooldown(datetime.now(UTC), cooldown)
            requested = retry_after_seconds(exc)
            source = ""
            if requested is not None:
                human = format_delay(timedelta(seconds=requested))
                source = f" (Retry-After demandé par Cloudflare : {human})"
            if cooldown > timedelta(0):
                logger.error(
                    "Discord bloque cette adresse IP (HTTP 429 sans en-tête Via : réponse "
                    "Cloudflare). Ce n'est pas une limite de débit de l'API mais un blocage "
                    "réseau, souvent causé par des redémarrages en boucle. Le bot ne "
                    "contactera plus Discord pendant %s%s ; les redémarrages de l'hébergeur "
                    "pendant cette pause seront ignorés. Si le blocage dure, change "
                    "d'adresse IP (ou demande à l'hébergeur) : insister ne sert à rien. "
                    "Détails : %s",
                    format_delay(cooldown),
                    source,
                    describe_http_error(exc),
                )
            else:
                logger.error(
                    "Discord bloque cette adresse IP (HTTP 429 sans en-tête Via : réponse "
                    "Cloudflare). LOGIN_COOLDOWN_SECONDS=0 et aucun Retry-After : les "
                    "redémarrages vont donc réessayer immédiatement, ce qui peut prolonger "
                    "le blocage. Change d'adresse IP ou attends qu'il expire. Détails : %s",
                    describe_http_error(exc),
                )
        else:
            logger.warning(
                "Discord est temporairement indisponible (HTTP %s) ; "
                "le superviseur pourra réessayer avec un délai. Détails : %s",
                exc.status,
                describe_http_error(exc),
            )
        return code
    except Exception:
        logger.exception("Erreur fatale au démarrage")
        return EXIT_TRANSIENT

    return EXIT_OK


if __name__ == "__main__":
    # Console script : ``main`` renvoie désormais un code de sortie explicite.
    raise SystemExit(main())
