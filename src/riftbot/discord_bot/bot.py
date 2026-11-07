from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
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
        enable_betting: bool = False,
        bot_channel_id: int | None = None,
        sync_cache: Path | None = None,
        force_sync: bool = False,
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
        self._enable_betting = enable_betting
        self._bot_channel_id = bot_channel_id
        # Empreinte des commandes déjà envoyées à Discord. Elle évite de refaire
        # un appel d'écriture (enregistrement des slash commands) à chaque
        # redémarrage, et donc d'alimenter une éventuelle boucle de crash.
        self._sync_cache = sync_cache
        self._force_sync = force_sync
        self.tree.error(self._on_app_command_error)

    async def setup_hook(self) -> None:
        if self._bot_channel_id is not None:
            # discord.py expose ``interaction_check`` comme une méthode ; on la remplace
            # à l'instance pour appliquer le filtre de salon à toutes les commandes.
            self.tree.interaction_check = self._check_bot_channel  # type: ignore[method-assign]
        await self.register_components()
        await self.sync_commands()

    async def _check_bot_channel(self, interaction: discord.Interaction[Any]) -> bool:
        """Refuse les commandes hors du salon configuré, sans jamais lever."""
        if self._bot_channel_id is None or interaction.channel_id == self._bot_channel_id:
            return True
        channel = interaction.channel
        if isinstance(channel, discord.Thread) and channel.parent_id == self._bot_channel_id:
            return True
        await send_error(
            interaction,
            f"Ce bot est limité au salon <#{self._bot_channel_id}>.",
        )
        return False

    async def register_components(self) -> None:
        await self.add_cog(LadderCog(self.services))
        if self._enable_betting:
            await self.add_cog(BettingCog(self.services))
        await self.add_cog(AdminCog(self.services))
        if self._dev_commands:
            await self.add_cog(DevCog(self.services))
            logger.warning("Commandes /dev activées : ne pas utiliser en production")
        self.add_dynamic_items(ConfirmResultButton, DisputeResultButton)

    async def sync_commands(self) -> None:
        """Synchronise les commandes, en évitant les écritures inutiles.

        L'enregistrement des slash commands est un appel d'écriture soumis à ses
        propres limites chez Discord. Le refaire à chaque démarrage ne sert à
        rien et peut, en cas de boucle de redémarrage, ressembler à un abus.
        On calcule donc une empreinte des commandes et on ne synchronise que si
        elle a changé (ou si ``force_sync`` / ``FORCE_COMMAND_SYNC`` est actif).
        """
        guild: discord.Object | None = None
        if self._dev_guild_id is not None:
            # Synchronisation instantanée sur le serveur de dev (la globale peut prendre 1 h).
            guild = discord.Object(id=self._dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            target = f"guild:{self._dev_guild_id}"
        else:
            target = "global"

        try:
            fingerprint = self._sync_fingerprint(target)
            if not self._force_sync and self._read_sync_cache() == fingerprint:
                logger.info("Commandes inchangées, synchronisation ignorée")
                return
            synced = await self.tree.sync(guild=guild)
        except Exception:
            # On ne laisse volontairement pas l'erreur tuer le démarrage : un
            # 429, un 5xx, un timeout réseau ou un refus de permission ne doit
            # pas provoquer de boucle Docker. Les commandes déjà enregistrées
            # restent utilisables ; on retentera au prochain démarrage.
            logger.exception(
                "Synchronisation des commandes impossible ; "
                "les commandes déjà enregistrées restent actives"
            )
            return

        logger.info("%d commandes synchronisées", len(synced))
        self._write_sync_cache(fingerprint)

    def _sync_fingerprint(self, target: str) -> str:
        """Empreinte stable de l'application, de la cible et des commandes."""
        payload = [command.to_dict(self.tree) for command in self.tree.get_commands()]
        material = json.dumps(
            {
                "application_id": self.application_id,
                "target": target,
                "commands": payload,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _read_sync_cache(self) -> str | None:
        """Empreinte précédemment enregistrée, ou ``None`` si illisible."""
        if self._sync_cache is None:
            return None
        try:
            return self._sync_cache.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None

    def _write_sync_cache(self, fingerprint: str) -> None:
        """Enregistre l'empreinte de façon atomique, sans bloquer le démarrage."""
        if self._sync_cache is None:
            return
        try:
            self._sync_cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._sync_cache.with_name(f"{self._sync_cache.name}.tmp")
            temporary.write_text(f"{fingerprint}\n", encoding="utf-8")
            temporary.replace(self._sync_cache)
        except OSError:
            # Un volume en lecture seule ne doit pas empêcher le bot de tourner.
            logger.warning(
                "Impossible d'enregistrer le cache de synchronisation dans %s",
                self._sync_cache,
                exc_info=True,
            )

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
        elif isinstance(original, app_commands.CommandOnCooldown):
            # Message dédié : sans lui, un cooldown serait présenté à tort comme
            # un problème de permissions.
            await send_error(
                interaction,
                f"Patiente encore {original.retry_after:.0f} s avant de réessayer.",
            )
        elif isinstance(error, app_commands.CheckFailure):
            await send_error(interaction, "Tu n'as pas la permission d'utiliser cette commande.")
        else:
            logger.exception("Erreur dans /%s", name, exc_info=original)
            await send_error(interaction, "Erreur inattendue, réessaie plus tard.")
