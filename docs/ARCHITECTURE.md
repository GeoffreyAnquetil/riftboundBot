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
│   ├── ladder/rules.py     # LadderRule (stratégie) + AdjacentPairsRule (mock)
│   ├── economy/models.py   # Wallet, Transaction (registre), Bet
│   └── economy/payout.py   # PayoutStrategy + ParimutuelPayout
├── application/
│   ├── ports.py            # interfaces dont dépendent les services
│   ├── ladder_service.py
│   ├── betting_service.py
│   ├── dto.py              # objets de lecture renvoyés à la présentation
│   └── queries.py          # lectures partagées entre services
├── infrastructure/
│   ├── sql/                # modèles ORM, repositories, UnitOfWork, migrations Alembic
│   ├── memory/             # mêmes ports, en mémoire (tests, prototypage)
│   ├── event_bus.py
│   └── clock.py
├── discord_bot/
│   ├── bot.py              # client, synchronisation des commandes, gestion d'erreurs
│   ├── cogs/               # ladder.py, betting.py, admin.py
│   └── views/              # embeds.py, result.py (modale + boutons persistants)
├── bootstrap.py            # racine de composition
├── config.py               # variables d'environnement (pydantic-settings)
└── __main__.py             # point d'entrée
```

## Principes

- **Paradigme** : objet pour la structure, fonctions pures pour les règles (« functional core,
  imperative shell »). La rotation du ladder et le calcul des gains sont des fonctions sans
  effet de bord, testées sans base ni Discord.
- **Repository + Unit of Work** : chaque cas d'usage ouvre une transaction
  (`async with uow_factory() as uow:`) et la valide avec `commit()`. Sortir du bloc sans commit
  annule tout. Les repositories renvoient des copies : il faut appeler `update()` pour
  enregistrer une modification.
- **Strategy** : `LadderRule` (appariement et évolution du classement) et `PayoutStrategy`
  (gains) sont des protocoles. Pour en changer, on injecte une autre classe dans
  `bootstrap.py`.
- **Événements de domaine** : `LadderService` publie `MatchConfirmed` et `MatchVoided` après son
  commit, et `BettingService` s'y abonne. Le ladder ne connaît donc pas les paris : chaque
  module peut évoluer seul.
- **Erreurs** : une `DomainError` porte un message destiné à l'utilisateur, que la couche
  Discord affiche en message éphémère. Toute autre exception est un bug : elle est journalisée
  et l'utilisateur voit un message générique.

## Règles métier actuelles

| Sujet | Règle |
|---|---|
| Appariement | **Mock** : 1v2, 3v4… Le gagnant prend la meilleure place ; avec un nombre impair, le dernier est exempt |
| Format | BO3 par défaut (`BEST_OF`) : 2-0 ou 2-1, pas de nul |
| Résultat | Déclaré par un joueur, puis confirmé par l'adversaire. Deux déclarations identiques valent confirmation, deux différentes ouvrent un litige |
| Auto-validation | Résultat sans réponse validé au bout de 24 h (`AUTO_CONFIRM_HOURS`) ou à la clôture |
| Match non joué | Annulé à la clôture : pas de changement de place, paris remboursés |
| Litige | Bloque la clôture jusqu'à `/admin set-result` |
| Paris | Pari mutuel : la cagnotte est partagée entre les gagnants au prorata (aucune monnaie créée). Remboursement si personne n'a misé sur le vainqueur |
| Fermeture des paris | Au premier résultat déclaré |
| Restrictions | Pas de pari sur son propre match, un seul pari par match et par personne |
| Solde | 1000 au premier accès (`STARTING_BALANCE`) ; les non-inscrits au ladder peuvent parier |
| Historique | Le classement est enregistré à l'ouverture de chaque semaine (`ranking_snapshots`) |

## Données

Toutes les tables ont un `guild_id` : plusieurs serveurs Discord peuvent partager la même base.

- `players`, `weeks`, `matches`, `ranking_snapshots` pour le ladder ;
- `wallets`, `transactions` et `bets` pour l'économie. `transactions` est un registre où l'on
  ne fait qu'ajouter des lignes : la somme des transactions d'un membre est égale à son solde,
  et les tests le vérifient.
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

### Changer la règle de ladder

1. Créer une classe qui respecte le protocole `LadderRule` dans `domain/ladder/rules.py`, avec
   deux méthodes : `pair(ranking, week_number)` et `apply(ranking, outcomes)`.
2. La tester dans `tests/unit/domain/test_rules.py`.
3. Remplacer `AdjacentPairsRule()` par la nouvelle règle dans `bootstrap.py`.

Le paramètre `week_number` permet par exemple de décaler les groupes une semaine sur deux.

### Passer à Postgres

1. Ajouter le pilote : `uv add asyncpg`.
2. Définir `DATABASE_URL=postgresql+asyncpg://user:motdepasse@hote/base`.

Les migrations et les repositories restent les mêmes.
