from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration lue depuis les variables d'environnement (ou le fichier ``.env``)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    discord_token: SecretStr | None = None
    # Serveur de développement : les commandes y sont synchronisées instantanément.
    dev_guild_id: int | None = None
    # Commandes /dev (faux joueurs) pour tester seul. Jamais en production.
    enable_dev_commands: bool = False
    # Salon unique autorisé pour les commandes du bot. Vide = aucun filtre.
    # Utile pour isoler un bot de test dans un seul salon.
    bot_channel_id: int | None = None
    # Liste blanche des identifiants Discord autorisés à utiliser /admin, séparés par des
    # virgules. Vide = uniquement les membres avec la permission « Gérer le serveur ».
    admin_user_ids: str = ""
    database_url: str = "sqlite+aiosqlite:///data/riftbot.db"

    # --- Garde-fous de démarrage / redémarrage -----------------------------------------------
    # État des démarrages récents. Le fichier est créé automatiquement.
    startup_guard_file: Path = Path("data/startup.json")
    # Nombre de démarrages autorisés dans la fenêtre. 0 désactive le garde-fou.
    startup_max_attempts: int = 5
    # Fenêtre glissante, en secondes. 0 désactive le garde-fou.
    startup_window_seconds: int = 600
    # Pause imposée après un HTTP 429 renvoyé par Cloudflare (blocage d'IP).
    # Pendant ce délai, un redémarrage sort immédiatement sans contacter
    # Discord. Si Cloudflare annonce une durée plus longue dans Retry-After,
    # c'est elle qui s'applique. 0 désactive la pause (déconseillé).
    login_cooldown_seconds: int = 1800
    # Plafond de la pause, même si Cloudflare demande davantage (secondes).
    login_max_cooldown_seconds: int = 86400
    # Tentatives de connexion dans le même processus sur une erreur transitoire
    # (HTTP 5xx). 1 désactive les nouvelles tentatives.
    login_max_attempts: int = 3
    # Délai initial avant une nouvelle tentative, en secondes. Il double à
    # chaque échec, sans dépasser login_max_retry_delay_seconds.
    login_retry_delay_seconds: float = 10.0
    login_max_retry_delay_seconds: float = 120.0
    # Empreinte des commandes déjà synchronisées : si elle n'a pas changé, on
    # évite un nouvel appel d'enregistrement à chaque redémarrage.
    command_sync_cache: Path = Path("data/command_sync.sha256")
    # Forcer la synchronisation même si l'empreinte est inchangée (après une
    # suppression manuelle des commandes dans Discord, par exemple).
    force_command_sync: bool = False

    best_of: int = 3
    auto_confirm_hours: int = 24

    # --- Ladder noté ----------------------------------------------------------------------------
    # Nombre de matchs de placement avant d'apparaître dans le classement principal.
    placement_matches: int = 4
    # Fenêtre de non-revanche : préférence forte, puis contrainte minimale.
    rematch_preferred_weeks: int = 4
    rematch_hard_weeks: int = 2
    # Nombre de semaines sans match avant le badge « inactif ».
    inactive_after_weeks: int = 3

    # --- Économie ------------------------------------------------------------------------------
    # Les paris ne font pas partie de la V1. Le code reste disponible pour une version
    # ultérieure ; les commandes et l'affichage ne sont activés que si ce drapeau est vrai.
    enable_betting: bool = False
    starting_balance: int = 1000

    log_level: str = "INFO"

    @field_validator("admin_user_ids")
    @classmethod
    def _validate_admin_user_ids(cls, value: str) -> str:
        for part in value.replace(",", " ").split():
            if not part.isdigit():
                raise ValueError(f"identifiant Discord invalide : {part!r}")
        return value

    @property
    def admin_ids(self) -> frozenset[int]:
        """Identifiants de la liste blanche, ou ensemble vide (permission Discord)."""
        return frozenset(int(part) for part in self.admin_user_ids.replace(",", " ").split())
