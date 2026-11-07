from pathlib import Path

from pydantic import SecretStr
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
    database_url: str = "sqlite+aiosqlite:///data/riftbot.db"

    # --- Garde-fous de démarrage / redémarrage -----------------------------------------------
    # État des démarrages récents. Le fichier est créé automatiquement.
    startup_guard_file: Path = Path("data/startup.json")
    # Nombre de démarrages autorisés dans la fenêtre. 0 désactive le garde-fou.
    startup_max_attempts: int = 5
    # Fenêtre glissante, en secondes. 0 désactive le garde-fou.
    startup_window_seconds: int = 600
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
