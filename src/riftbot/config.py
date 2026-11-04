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

    best_of: int = 3
    auto_confirm_hours: int = 24
    starting_balance: int = 1000

    log_level: str = "INFO"
