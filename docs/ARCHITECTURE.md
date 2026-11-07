# Architecture

Architecture hexagonale légère (*ports & adapters*). Une seule règle : **les dépendances
pointent vers le domaine**. Le domaine n'importe ni `discord` ni `sqlalchemy`.

```
 Discord (slash commands, modales, boutons)
        │
 ┌──────▼──────────────────┐
 │ discord_bot/            │  Présentation : cogs = contrôleurs fins, views/ = embeds et modales
 └──────┬──────────────────┘
        │ appelle
 ┌──────▼──────────────────┐
 │ application/            │  Cas d'usage : LadderService, BettingService
 │   ports.py (interfaces) │  Repositories, UnitOfWork, Clock, EventBus
 └──────┬──────────────────┘
        │ utilise
 ┌──────▼──────────────────┐
 │ domain/ (Python pur)    │  Entités, machine à états des matchs, règles de ladder, gains
 └─────────────────────────┘
        ▲ implémente les ports
 ┌──────┴──────────────────┐
 │ infrastructure/         │  sql/ (SQLAlchemy + Alembic), memory/ (tests), bus, horloge
 └─────────────────────────┘

 bootstrap.py : seul endroit qui choisit les implémentations concrètes et les assemble.
```

## Arborescence

```
src/riftbot/
├── domain/
│   ├── errors.py           # DomainError : message affiché tel quel à l'utilisateur
│   ├── events.py           # MatchConfirmed, MatchVoided
│   ├── ladder/models.py    # Player, Week, Match (machine à états), MatchFormat
│   ├── ladder/rating.py    # Glicko-2 : notes, incertitude, volatilité, inactivité
│   ├── ladder/pairing.py   # appariement hebdomadaire sans revanche + exempt
│   ├── economy/models.py   # Wallet, Transaction (registre), Bet
│   └── economy/payout.py   # PayoutStrategy + ParimutuelPayout
├── application/
│   ├── ports.py            # interfaces dont dépendent les services
│   ├── ladder_service.py
│   ├── simulation.py       # simulation de semaines pour /dev simulate
│   ├── betting_service.py  # hors V1, conservé pour plus tard
│   ├── dto.py              # objets de lecture renvoyés à la présentation
│   └── queries.py          # lectures partagées entre services
├── infrastructure/
│   ├── sql/                # modèles ORM, repositories, UnitOfWork, migrations Alembic
│   ├── memory/             # mêmes ports, en mémoire (tests, prototypage)
│   ├── event_bus.py
│   └── clock.py
├── discord_bot/
│   ├── bot.py              # client, synchronisation des commandes, gestion d'erreurs
│   ├── cogs/               # ladder.py, admin.py, dev.py (+ betting.py hors V1)
│   └── views/              # embeds.py, result.py (modale + boutons persistants)
├── bootstrap.py            # racine de composition
├── config.py               # variables d'environnement (pydantic-settings)
└── __main__.py             # point d'entrée
```

## Principes

- **Paradigme** : objet pour la structure, fonctions pures pour les règles (« functional core,
  imperative shell »). L'appariement, les notes Glicko-2 et le calcul des gains sont des
  fonctions sans effet de bord, testées sans base ni Discord.
- **Repository + Unit of Work** : chaque cas d'usage ouvre une transaction
  (`async with uow_factory() as uow:`) et la valide avec `commit()`. Sortir du bloc sans commit
  annule tout. Les repositories renvoient des copies : il faut appeler `update()` pour
  enregistrer une modification.
- **Fonctions pures pour les règles** : `ladder/rating.py` (Glicko-2) et
  `ladder/pairing.py` (couplage sans revanche) ne dépendent ni de la base ni de Discord.
  `LadderService` les appelle, mais la logique de note et d'appariement reste testable seule.
- **Strategy** : `PayoutStrategy` (gains) est un protocole. Les paris sont désactivés en V1
  (`ENABLE_BETTING=false`) mais le service reste branché pour une version ultérieure.
- **Événements de domaine** : `LadderService` publie `MatchConfirmed` et `MatchVoided` après son
  commit, et `BettingService` s'y abonne. Le ladder ne connaît donc pas les paris : chaque
  module peut évoluer seul.
- **Erreurs** : une `DomainError` porte un message destiné à l'utilisateur, que la couche
  Discord affiche en message éphémère. Toute autre exception est un bug : elle est journalisée
  et l'utilisateur voit un message générique.

## Règles métier actuelles

| Sujet | Règle |
|---|---|
| Rythme | Une semaine = un match par joueur. Les matchs sont générés par `/admin week-open` ; un joueur impair est exempt |
| Appariement | Couplage de coût minimal : on minimise la somme des écarts de note. On évite d'abord les adversaires des `REMATCH_PREFERRED_WEEKS` dernières semaines, puis des `REMATCH_HARD_WEEKS`, et on n'accepte une revanche qu'en dernier recours |
| Inscription | À tout moment. Pendant une semaine ouverte, un joueur non apparié peut recevoir un match de rattrapage contre un autre joueur libre ; sinon il entre dans la semaine suivante |
| Note | Glicko-2, mise à jour à la clôture avec les notes d'avant-semaine. Un match non joué ne change pas la note mais augmente l'incertitude |
| Placement | Les `PLACEMENT_MATCHES` premiers matchs ne placent pas le joueur dans le classement principal ; il apparaît dans la section « placement » |
| Inactivité | Après `INACTIVE_AFTER_WEEKS` semaines sans match, badge « inactif » et incertitude accrue |
| Format | BO3 par défaut (`BEST_OF`) : 2-0 ou 2-1, pas de nul |
| Résultat | Déclaré par un joueur, puis confirmé par l'adversaire. Deux déclarations identiques valent confirmation, deux différentes ouvrent un litige |
| Auto-validation | Résultat sans réponse validé au bout de 24 h (`AUTO_CONFIRM_HOURS`) ou à la clôture |
| Match non joué | Annulé à la clôture : pas de changement de note |
| Litige | Bloque la clôture jusqu'à `/admin set-result` |
| Paris | **Hors V1** (`ENABLE_BETTING=false`). Le code du pari mutuel reste disponible pour une version ultérieure |
| Historique | Le classement est enregistré à l'ouverture de chaque semaine (`ranking_snapshots`) |

## Données

Toutes les tables ont un `guild_id` : plusieurs serveurs Discord peuvent partager la même base.

- `players` : identité, rang courant (`rank`), note Glicko-2 (`rating`,
  `rating_deviation`, `rating_volatility`), compteurs `rated_matches`, `byes`,
  `inactive_weeks` ;
- `weeks`, `matches`, `ranking_snapshots` pour le ladder. `matches.rated` garantit qu'une note
  n'est appliquée qu'une fois ; `matches.version` verrouille les résultats concurrents ;
- `wallets`, `transactions` et `bets` pour l'économie, conservés mais inutilisés en V1.
  `transactions` est un registre où l'on ne fait qu'ajouter des lignes : la somme des
  transactions d'un membre est égale à son solde, et les tests le vérifient.
- `matches` et `wallets` ont une colonne `version` (verrouillage optimiste). Deux modifications
  concurrentes ne peuvent pas s'écraser : la seconde lève `ConcurrentModification`.

## Recettes

### Ajouter une commande

1. Si la commande touche au métier, ajouter une méthode au service concerné, avec ses tests dans
   `tests/application/`.
2. Ajouter la commande dans le cog adapté (`discord_bot/cogs/`), et la mise en forme dans
   `views/embeds.py`.
3. Si c'est une nouvelle commande de premier niveau, l'ajouter à `EXPECTED_COMMANDS` dans
   `tests/unit/test_discord_wiring.py`.

### Régler l'appariement ou la notation

1. Les paramètres sont dans `config.py` : `PLACEMENT_MATCHES`, `REMATCH_PREFERRED_WEEKS`,
   `REMATCH_HARD_WEEKS`, `INACTIVE_AFTER_WEEKS`.
2. La logique pure est dans `domain/ladder/pairing.py` et `domain/ladder/rating.py` ; la tester
   dans `tests/unit/domain/test_pairing.py` et `tests/unit/domain/test_rating.py`.
3. `LadderService` ne fait que charger les données, appeler ces fonctions et persister le
   résultat.

Pour un autre système de note, remplacer `rating.py` en conservant les mêmes entrées/sorties
(`Rating`, `update_match`, `inflate`) ; les tests de service continuent de couvrir le câblage.

### Passer à Postgres

1. Ajouter le pilote : `uv add asyncpg`.
2. Définir `DATABASE_URL=postgresql+asyncpg://user:motdepasse@hote/base`.

Les migrations et les repositories restent les mêmes.
