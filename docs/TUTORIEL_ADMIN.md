# Riftbot — Guide de l'admin

Les commandes `/admin` sont réservées par défaut aux membres ayant la permission **Gérer le
serveur** (modifiable dans *Paramètres du serveur > Intégrations*). Si le propriétaire du bot a
renseigné `ADMIN_USER_IDS`, seuls les identifiants de cette liste peuvent les utiliser.

## Le rythme d'une semaine

1. **`/admin week-open`** — apparie les joueurs, crée les matchs et publie la semaine en
   mentionnant tous les participants.
2. Les joueurs jouent et déclarent leurs résultats avec `/myresult`. L'adversaire confirme
   ou conteste ; sans réponse, le résultat est validé après 24 h.
3. **`/admin week-close`** — valide les résultats en attente, annule les matchs non joués,
   applique les notes Glicko-2 et publie le nouveau classement.

> ⚠️ La clôture est **définitive** : les notes sont appliquées et les résultats ne peuvent
> plus être modifiés. Tranche les litiges **avant** de clôturer.

## Commandes

| Commande | Rôle |
|---|---|
| `/admin week-open` | Ouvrir la semaine et générer les matchs |
| `/admin week-close` | Clôturer la semaine et mettre à jour les notes |
| `/admin refresh-ranks` | Recalculer les places sans attendre la clôture |
| `/admin set-result` | Trancher un litige ou saisir un forfait (match, vainqueur, score) |
| `/admin correct-result` | Corriger un résultat via deux listes déroulantes |
| `/admin remove-player` | Retirer un joueur du ladder |

## Gérer les litiges

- Un match **en litige** bloque `/admin week-close` : le bot indique les matchs concernés.
- **`/admin set-result`** : choisis le match, le vainqueur et le score (`2-1`, `2-0`…).
  Pratique aussi pour un forfait.
- **`/admin correct-result`** : sélectionne le match dans une liste, puis le nouveau
  résultat. Le bot publie ensuite un message public qui **mentionne les deux joueurs**.
  La correction n'est possible que tant que la semaine est **ouverte**.

## Retirer un joueur

`/admin remove-player` retire le joueur du ladder. Son match en cours est annulé et son
adversaire est réapparié avec un joueur libre si possible.

## Points d'attention

- **Un seul match par joueur et par semaine.** Si l'effectif est impair, un joueur est
  exempt : l'exempt est indiqué dans `/matches`.
- Les **4 premiers matchs** d'un joueur sont des matchs de placement : il n'entre dans le
  classement principal qu'ensuite. Après un changement de `PLACEMENT_MATCHES`,
  `/admin refresh-ranks` replace tout le monde immédiatement.
- `/matches` affiche l'état de chaque match : à jouer, en attente, litige, annulé.
- `/admin week-open` mentionne tous les joueurs : préviens le serveur avant de lancer.
- Si `BOT_CHANNEL_ID` est configuré, le bot ne répond que dans ce salon.
- En cas de doute sur un match, `/admin set-result` permet de fixer le résultat avant la
  clôture.
