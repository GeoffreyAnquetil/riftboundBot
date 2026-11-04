# Riftbot

Bot Discord de ladder hebdomadaire et de paris pour le TCG Riftbound.

- **Ladder** : inscription, matchs générés chaque semaine depuis le classement, déclaration et
  confirmation des résultats, classement mis à jour à la clôture.
- **Paris** : monnaie virtuelle, paris sur les matchs de la semaine (pari mutuel), classement des
  plus riches.

> ⚠️ La règle d'appariement est un **mock** : 1 contre 2, 3 contre 4… et le gagnant prend la
> meilleure place. La vraie règle se branchera dans `bootstrap.py` (voir
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#changer-la-règle-de-ladder)).

## Commandes

| Commande | Rôle |
|---|---|
| `/register` | Rejoindre le ladder (en bas du classement) |
| `/leaderboard` | Classement actuel, avec victoires et défaites |
| `/matches` | Matchs de la semaine, résultats, cagnottes et cotes |
| `/myresult` | Déclarer le résultat de son match (modale) ; l'adversaire confirme ou conteste |
| `/profile [membre]` | Rang, meilleur rang, bilan, solde, derniers matchs |
| `/bet <match> <vainqueur> <mise>` | Parier sur un match pas encore joué |
| `/balance` | Son solde |
| `/richest` | Classement des plus riches |
| `/mybets` | Ses derniers paris |
| `/admin week-open` | Ouvrir une semaine et générer les matchs |
| `/admin week-close` | Clôturer la semaine et mettre à jour le classement |
| `/admin set-result` | Trancher un litige, saisir un forfait |
| `/admin remove-player` | Retirer un joueur du ladder |

Les commandes `/admin` sont réservées par défaut aux membres qui ont la permission « Gérer le
serveur » (modifiable dans *Paramètres du serveur > Intégrations*).

### Déroulement d'une semaine

1. Un admin lance `/admin week-open` : les matchs sont générés et publiés, et les paris ouvrent.
2. Après son match, un joueur lance `/myresult` et choisit son score. L'adversaire reçoit des
   boutons **Confirmer** / **Contester**. Il peut aussi déclarer lui-même : deux déclarations
   identiques valent confirmation, deux déclarations différentes ouvrent un litige.
3. Le premier résultat déclaré ferme les paris sur ce match. Un résultat sans réponse est
   validé automatiquement au bout de 24 h. À la validation, les gains sont versés.
4. `/admin week-close` traite tout ce qui reste :
   - un résultat déclaré mais pas confirmé est validé ;
   - un match non joué est annulé et ses paris remboursés ;
   - un litige bloque la clôture tant qu'un admin ne l'a pas tranché avec `/admin set-result`.

   Le nouveau classement est ensuite publié.

## Installation

Prérequis : Python 3.12 et [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uv sync                  # crée .venv et installe les dépendances
cp .env.example .env     # puis renseigner DISCORD_TOKEN et DEV_GUILD_ID
uv run riftbot           # applique les migrations puis lance le bot
```

### Créer le bot Discord

1. Sur le [Developer Portal](https://discord.com/developers/applications), créer une application
   *New Application*, puis dans l'onglet *Bot* cliquer sur *Reset Token* et copier le token dans
   `.env`.
2. Inviter le bot avec l'URL suivante, en remplaçant `CLIENT_ID` par l'*Application ID* :
   `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=19456`.
   Elle donne les permissions Voir les salons, Envoyer des messages et Intégrer des liens.
3. Pour le développement, mettre l'ID du serveur de test dans `DEV_GUILD_ID` : les commandes y
   apparaissent immédiatement. Pour copier cet ID, activer le mode développeur dans Discord,
   puis faire clic droit sur le serveur > *Copier l'identifiant*.

### Docker

```bash
docker compose up -d --build   # la base SQLite est stockée dans ./data
```

## Développement

```bash
uv run pytest              # tests (services rejoués sur la base mémoire ET SQLite)
uv run ruff format         # formatage
uv run ruff check          # lint
uv run mypy                # typage strict
```

La CI (GitHub Actions) lance ces quatre vérifications sur chaque PR.

**Modifier le schéma de la base** : modifier `src/riftbot/infrastructure/sql/models.py`, puis
générer la migration et la relire avant de la committer :

```bash
uv run alembic revision --autogenerate -m "ajoute la colonne X"
```

Les migrations sont appliquées automatiquement au démarrage du bot. Un test échoue si un modèle
a changé sans migration.

### Workflow Git

- `main` est toujours déployable ; on n'y pousse pas directement.
- Une branche par sujet (`feat/paris-cotes`, `fix/clôture-litiges`…), puis une PR vers `main`.
- La CI doit être verte avant de merger.

L'architecture et les conventions sont décrites dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
