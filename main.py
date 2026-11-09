"""Point d'entrée pour les hébergeurs qui lancent un fichier Python (Wispbyte, Pterodactyl…).

Le paquet vit dans ``src/`` et n'est pas installé sur ces plateformes : on ajoute ce dossier
au chemin d'import, puis on appelle le vrai point d'entrée. La commande de démarrage est donc
simplement ``python main.py``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

if sys.version_info < (3, 12):  # noqa: UP036 — garde-fou pour les hébergeurs
    raise SystemExit(
        "Riftbot nécessite Python 3.12 ou plus récent "
        f"(version détectée : {sys.version.split()[0]}). "
        "Choisis l'image Docker Python 3.12 dans les réglages du serveur."
    )

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

entry_point = importlib.import_module("riftbot.__main__")
raise SystemExit(entry_point.main())
