# Déployer Riftbot sur Wispbyte

Guide court pour un serveur Wispbyte (panneau type Pterodactyl). Un seul bot par serveur.

## Prérequis

- Le dépôt poussé sur GitHub (ou un ZIP du projet **sans** `.venv/`, `.git/`, `data/`).
- Le token du bot Discord.
- L'image Docker **Python 3.12** (le bot utilise la syntaxe Python 3.12 ; 3.11 ne suffit pas).

## Configuration du serveur

1. **Create Server** → technologie **Python**.
2. **Startup → Docker Image** : choisis **Python 3.12**.
3. Envoie le code : **GitHub Integration** (recommandé, voir ci-dessous) ou Files → Upload
   d'un ZIP décompressé.
4. **Startup** :
   - fichier de démarrage : `main.py` ;
   - fichier de dépendances : `requirements.txt`.
5. **Environment Variables** (ou un fichier `.env` à la racine) :

   | Variable | Valeur |
   |---|---|
   | `DISCORD_TOKEN` | **obligatoire** — token du bot |
   | `DEV_GUILD_ID` | vide en production |
   | `ENABLE_DEV_COMMANDS` | `false` |
   | `ENABLE_BETTING` | `false` |

   Les autres réglages ont des valeurs par défaut correctes (`DATABASE_URL`, `BEST_OF=3`,
   `PLACEMENT_MATCHES=4`, etc.).

6. Clique **Start** et surveille la console : les migrations s'appliquent au démarrage,
   puis `Connecté en tant que …` doit apparaître.

> Les commandes slash globales peuvent mettre jusqu'à 1 h à apparaître dans Discord. Pour
> les tester immédiatement sur un serveur de test, mets son ID dans `DEV_GUILD_ID` le temps
> du test, puis vide-le.

## Déploiement continu via GitHub

C'est la méthode recommandée : Wispbyte clone le dépôt et fait un `git pull` à chaque
démarrage.

1. Pousse `main.py`, `requirements.txt` et le code sur la branche de déploiement (`main` par
   défaut, ou une branche dédiée).
2. Dans Wispbyte : **GitHub Integration** → connecte le dépôt → choisis la branche → **Pull**.
   Pour un dépôt privé, renseigne le token ou la clé sur cette page.
3. **Startup** : garde la commande Python par défaut (elle enchaîne `git pull`, l'installation
   de `requirements.txt`, puis le lancement) et règle :
   - fichier de démarrage : `main.py` ;
   - fichier de dépendances : `requirements.txt` ;
   - `AUTO_UPDATE=1` si l'option est proposée.
4. À chaque livraison : merge sur la branche, puis **Pull + Restart** (ou simple redémarrage
   si `AUTO_UPDATE=1`).

Au redémarrage : `git pull` → `pip install -r requirements.txt` → `python main.py`
(migrations Alembic puis bot).

### Règles

- Ne modifie pas les fichiers suivis depuis le file manager : un `git pull` échouerait sur un
  conflit. Seuls `.env` et `data/` sont à créer/éditer sur le serveur (ils ne sont pas suivis).
- `.env` et `data/` ne sont pas dans le dépôt : ils survivent aux pulls.
- Si tu changes les dépendances, régénère `requirements.txt` et commit-le : la CI échoue si
  le fichier ne correspond plus à `uv.lock`.
- Retour arrière : choisis un autre commit/branche dans le panneau, ou revert sur GitHub puis
  Pull.

## Données et sauvegardes

La base SQLite est dans `data/riftbot.db`, dans le dossier persistant du serveur. Télécharge
régulièrement ce fichier depuis **Files** pour faire une sauvegarde. Ne supprime pas le
dossier `data/` entre deux démarrages.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| `SyntaxError` ou message « Python 3.12 ou plus récent » | Mauvaise image : sélectionne Python 3.12 |
| `ModuleNotFoundError: discord` | `requirements.txt` absent ou install non lancée : vérifie Startup |
| `Token Discord refusé` | `DISCORD_TOKEN` absent ou invalide |
| Le bot refuse de démarrer après plusieurs crashs | Garde-fou anti-boucle : supprime `data/startup.json` ou attends 10 min |
| `Missing Main File` | Sélectionne `main.py` comme fichier de démarrage |

Le panneau ne donne pas de shell : tout passe par **Startup** (image, fichier, paquets,
variables) et **Files**.

## Mettre à jour les dépendances

Après une modification de `pyproject.toml`, régénère le fichier utilisé par Wispbyte :

```bash
uv export --locked --no-dev --no-hashes --no-emit-project -o requirements.txt
```
