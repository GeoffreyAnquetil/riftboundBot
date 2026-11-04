from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from riftbot.bootstrap import Services
from riftbot.discord_bot.cogs.admin import AdminCog
from riftbot.discord_bot.cogs.betting import BettingCog
from riftbot.discord_bot.cogs.dev import DevCog
from riftbot.discord_bot.cogs.ladder import LadderCog
from riftbot.discord_bot.common import interaction_age, is_expired, send_error
from riftbot.discord_bot.views.result import ConfirmResultButton, DisputeResultButton
from riftbot.domain.errors import DomainError

logger = logging.getLogger(__name__)


class RiftBot(commands.Bot):
    def __init__(
        self,
        services: Services,
        *,
        dev_guild_id: int | None = None,
        dev_commands: bool = False,
    ) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
            allowed_contexts=app_commands.AppCommandContext(
                guild=True, dm_channel=False, private_channel=False
            ),
        )
        self.services = services
        self._dev_guild_id = dev_guild_id
        self._dev_commands = dev_commands
        self.tree.error(self._on_app_command_error)

    async def setup_hook(self) -> None:
        await self.register_components()
        await self.sync_commands()

    async def register_components(self) -> None:
        await self.add_cog(LadderCog(self.services))
        await self.add_cog(BettingCog(self.services))
        await self.add_cog(AdminCog(self.services))
        if self._dev_commands:
            await self.add_cog(DevCog(self.services))
            logger.warning("Commandes /dev activées : ne pas utiliser en production")
        self.add_dynamic_items(ConfirmResultButton, DisputeResultButton)

    async def sync_commands(self) -> None:
        if self._dev_guild_id is not None:
            # Synchronisation instantanée sur le serveur de dev (la globale peut prendre 1 h).
            guild = discord.Object(id=self._dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        logger.info("%d commandes synchronisées", len(synced))

    async def on_ready(self) -> None:
        logger.info("Connecté en tant que %s", self.user)

    async def _on_app_command_error(
        self, interaction: discord.Interaction[Any], error: app_commands.AppCommandError
    ) -> None:
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        name = interaction.command.qualified_name if interaction.command else "?"
        if is_expired(original):
            # Répondre est impossible : Discord a déjà abandonné l'interaction.
            logger.warning(
                "/%s : interaction expirée, %.1f s après sa création (Discord exige une "
                "réponse sous 3 s). Latence de la connexion au gateway : %.0f ms.",
                name,
                interaction_age(interaction),
                self.latency * 1000,
            )
        elif isinstance(original, DomainError):
            await send_error(interaction, str(original))
        elif isinstance(error, app_commands.CheckFailure):
            await send_error(interaction, "Tu n'as pas la permission d'utiliser cette commande.")
        else:
            logger.exception("Erreur dans /%s", name, exc_info=original)
            await send_error(interaction, "Erreur inattendue, réessaie plus tard.")
