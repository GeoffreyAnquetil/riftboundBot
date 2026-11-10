# Riftbot

Bot Discord de ladder hebdomadaire pour le TCG Riftbound.

- **Ladder noté** : appariement chaque semaine, un match par joueur, notes Glicko-2 mises à
  jour à la clôture.
- **Inscription à tout moment** : un joueur peut rejoindre pendant une semaine ouverte ; s'il
  reste un adversaire libre, il joue la même semaine, sinon il entre dans l'appariement suivant.
- **Sans revanche** : l'appariement évite les adversaires rencontrés récemment, tout en gardant
  des matchs équilibrés.
- **Paris et monnaie** : hors V1 (`ENABLE_BETTING=false`). Le code reste dans le dépôt pour une
  version ultérieure, mais aucune commande ni aucun affichage de pièces n'est actif.

## Commandes

| Commande | Rôle |
|---|---|
| `/register` | Rejoindre le ladder (ou le réintégrer) |
| `/leaderboard` | Classement : joueurs classés puis joueurs en placement |
| `/matches` | Matchs de la semaine, notes, exempt |
| `/myresult` | Déclarer le résultat de son match (modale) ; l'adversaire confirme ou conteste |
| `/profile [membre]` | Rang, note, bilan, statut, derniers matchs |
| `/admin week-open` | Ouvrir une semaine et générer les matchs |
| `/admin week-close` | Clôturer la semaine et mettre à jour les notes |
| `/admin refresh-ranks` | Recalcule les places sans attendre la clôture |
| `/admin set-result` | Trancher un litige, saisir un forfait |
| `/admin correct-result` | Corriger un résultat de la semaine (listes déroulantes) |
| `/admin remove-player` | Retirer un joueur du ladder |

Les commandes `/admin` sont réservées par défaut aux membres qui ont la permission « Gérer le
serveur » (modifiable dans *Paramètres du serveur > Intégrations*). Pour une liste blanche
explicite, renseigne `ADMIN_USER_IDS` (identifiants Discord séparés par des virgules) : seuls
ces utilisateurs pourront lancer les commandes `/admin`, même sans cette permission.

### Déroulement d'une semaine

1. Un admin lance `/admin week-open` : chaque joueur actif reçoit exactement un match, ou un
   exempt s'il est impair. L'appariement minimise les écarts de note et évite les revanches
   récentes.
2. Après son match, un joueur lance `/myresult` et choisit son score. L'adversaire reçoit des
   boutons **Confirmer** / **Contester**. Deux déclarations identiques valent confirmation ;
   deux déclarations différentes ouvrent un litige.
3. Un résultat sans réponse est validé automatiquement au bout de 24 h.
4. `/admin week-close` traite tout ce qui reste :
   - un résultat déclaré mais pas confirmé est validé ;
   - un match non joué est annulé ;
   - un litige bloque la clôture tant qu'un admin ne l'a pas tranché avec `/admin set-result`.

   Les notes Glicko-2 sont ensuite mises à jour en une seule passe, puis le classement est
   publié.

### Corriger un résultat

Pendant une semaine ouverte, un admin peut changer le résultat de n'importe quel match avec
`/admin correct-result` : une liste déroulante propose les matchs de la semaine, puis le
nouveau résultat. Le bot publie ensuite un message public qui mentionne les deux joueurs.

`/admin set-result` fait la même chose avec des options classiques (match, vainqueur et
score). Une fois la semaine clôturée, les résultats sont définitifs : les notes ont été
appliquées. Tranche les litiges avant `/admin week-close`.

## Classement et appariement

- **Glicko-2** : chaque joueur a une note (`1500` au départ), une incertitude (`RD=350`) et une
  volatilité. Une victoire contre un joueur mieux noté rapporte plus qu'une victoire attendue ;
  l'incertitude diminue avec les matchs et remonte après une absence.
- **Placement** : les `PLACEMENT_MATCHES` premiers matchs (4 par défaut) servent à calibrer.
  Un joueur en placement est affiché à part et n'occupe pas le classement principal. Après
  avoir changé ce réglage, `/admin refresh-ranks` réaligne immédiatement les places.
- **Un match par semaine** : le service refuse deux matchs non annulés pour un même joueur
  dans la même semaine.
- **Sans revanche** : l'appariement essaie d'abord d'éviter tout adversaire des
  `REMATCH_PREFERRED_WEEKS` dernières semaines (4 par défaut), puis relâche à
  `REMATCH_HARD_WEEKS` (2), et n'accepte une revanche que si aucun autre appariement complet
  n'existe.
- **Note la plus élevée en haut** : le classement principal est trié par note, avec départage
  par incertitude puis expérience. Les joueurs en placement sont classés après les joueurs
  classés.
- **Inactivité** : après `INACTIVE_AFTER_WEEKS` semaines sans match, un badge `inactif`
  apparaît ; l'incertitude augmente, ce qui permet un recalibrage rapide au retour.

### Migration depuis l'ancien ladder

La migration ajoute les notes sans rejouer l'historique : tous les joueurs existants
démarrent à `1500` et passent par leurs `PLACEMENT_MATCHES` premiers matchs avant d'entrer
dans le classement principal. Les anciens matchs restent visibles dans `/profile`, mais ne
servent pas à calculer la note. Pour un serveur de test, le plus simple est de repartir d'une
base vide (`data/riftbot.db`).

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
4. Pour limiter le bot à un seul salon, renseigner `BOT_CHANNEL_ID` avec l'ID du salon (clic
   droit sur le salon > *Copier l'identifiant*). Toute commande lancée ailleurs reçoit un
   message éphémère indiquant le salon autorisé. Laisser vide pour autoriser tous les salons.

### Docker

```bash
docker compose up -d --build   # la base SQLite est stockée dans ./data
```

Au premier démarrage, le conteneur corrige automatiquement les droits de `./data` (Docker
le crée en root sur un hôte Linux) puis abandonne les privilèges root avant de lancer le
bot. Si tu lances le conteneur toi-même avec `--user 1000:1000`, crée le dossier avec les
bons droits : `mkdir -p data && sudo chown -R 1000:1000 data`.

### Wispbyte

Pour un hébergement Wispbyte, voir [docs/DEPLOIEMENT_WISPBITE.md](docs/DEPLOIEMENT_WISPBITE.md)
(image Python 3.12, `main.py`, `requirements.txt` et variables d'environnement).

### Redémarrages et limites Discord

Le conteneur utilise `restart: "on-failure:5"` : après cinq échecs consécutifs,
Docker s'arrête au lieu de boucler indéfiniment. En complément, le bot refuse de
contacter Discord après `STARTUP_MAX_ATTEMPTS` démarrages en
`STARTUP_WINDOW_SECONDS` secondes (5 en 600 s par défaut) et quitte avec le code
`78`. Cela évite qu'une erreur permanente (token invalide, scope manquant,
serveur de dev introuvable, base inaccessible) ne se transforme en flux de
connexions et en blocage Cloudflare/IP.

La synchronisation des commandes est ignorée quand leur empreinte n'a pas changé
(`data/command_sync.sha256`). Après une suppression manuelle des commandes dans
Discord, lancer une fois avec `FORCE_COMMAND_SYNC=true`.

Après un redémarrage de l'hôte, relancer explicitement `docker compose up -d`
(ou superviser le conteneur avec systemd).

## Tester seul avec de faux joueurs

Un match oppose deux comptes, c'est l'adversaire qui confirme, et le rythme est hebdomadaire.
Pour tout dérouler seul, mets `ENABLE_DEV_COMMANDS=true` dans `.env` et relance le bot.
Le groupe `/dev` apparaît alors, réservé aux admins (**jamais en production**) :

| Commande | Rôle |
|---|---|
| `/dev seed <nombre>` | Ajoute de faux joueurs (🤖 Alpha, 🤖 Bravo…) |
| `/dev simulate <semaines> [seed]` | Joue et clôture N semaines, puis affiche le classement final |
| `/dev report-as <joueur> <score>` | Déclare le résultat du match d'un faux joueur |
| `/dev answer-as <joueur> <décision>` | Confirme ou conteste à la place d'un faux joueur |

Scénario conseillé :

1. `/dev seed 7` : sept faux joueurs, tous en placement.
2. `/dev simulate 6` : six semaines complètes. Les notes se stabilisent, les premiers joueurs
   sortent du placement et le classement se remplit.
3. Pour tester à la main : `/admin week-open`, puis `/dev report-as` et `/dev answer-as`.
   `/admin week-close` applique les notes et publie les mouvements.
4. Pour tester un litige : `/dev report-as` avec un score, puis `/dev answer-as` avec
   l'autre score. `/admin week-close` est refusé tant qu'un admin n'a pas tranché.

La simulation est reproductible avec une `seed` : relancer avec la même graine et le même
effectif rejoue les mêmes résultats.

Pour repartir de zéro, arrête le bot et supprime `data/riftbot.db`.

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
- Une branche par sujet (`feat/appariement-note`, `fix/clôture-litiges`…), puis une PR vers
  `main`.
- La CI doit être verte avant de merger.

L'architecture et les conventions sont décrites dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
