"""Point d'entrée : ``uv run riftbot`` ou ``python -m riftbot``."""

import asyncio
import contextlib
import logging
import sys

import discord

from riftbot.bootstrap import build_sql_services
from riftbot.config import Settings
from riftbot.discord_bot.bot import RiftBot
from riftbot.infrastructure.sql.database import upgrade_database

logger = logging.getLogger("riftbot")


async def run(settings: Settings, token: str) -> None:
    services, engine = build_sql_services(settings)
    bot = RiftBot(
        services,
        dev_guild_id=settings.dev_guild_id,
        dev_commands=settings.enable_dev_commands,
    )
    try:
        async with bot:
            await bot.start(token)
    finally:
        await engine.dispose()


def main() -> None:
    settings = Settings()
    discord.utils.setup_logging(level=logging.getLevelName(settings.log_level.upper()))
    discord.VoiceClient.warn_nacl = False  # le bot n'utilise pas la voix
    if settings.discord_token is None or not settings.discord_token.get_secret_value():
        sys.exit("DISCORD_TOKEN manquant : copie .env.example en .env et renseigne-le.")

    logger.info("Migrations de la base…")
    upgrade_database(settings.database_url)

    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run(settings, settings.discord_token.get_secret_value()))
    except discord.LoginFailure:
        sys.exit("Token Discord refusé : vérifie DISCORD_TOKEN dans .env.")


if __name__ == "__main__":
    main()
