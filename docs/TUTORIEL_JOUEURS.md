# Riftbot — Guide du joueur

Bot Discord de ladder hebdomadaire pour le TCG Riftbound.

## Rejoindre le ladder

1. Lance `/register`.
2. Si une semaine est en cours et qu'un adversaire est libre, tu reçois un match tout de
   suite. Sinon, tu entres dans l'appariement de la semaine suivante.

## Jouer ta semaine

1. `/matches` : affiche ton adversaire et l'état de ton match.
2. Jouez votre partie en **BO3** (deux manches gagnantes : `2-0` ou `2-1`).
3. `/myresult` : choisis le score **de ton côté**.
4. Ton adversaire reçoit deux boutons :
   - **Confirmer** → le résultat est validé ;
   - **Contester** → un litige est ouvert, un admin doit trancher.

Cas particuliers :

- Si vous déclarez tous les deux le même score, le match est validé automatiquement.
- Sans réponse de l'adversaire, le résultat est validé au bout de **24 h**.
- Tu peux corriger ta déclaration avec `/myresult` tant qu'elle n'est pas validée.

## Commandes

| Commande | Rôle |
|---|---|
| `/register` | Rejoindre le ladder (ou le réintégrer) |
| `/matches` | Matchs de la semaine, scores et exempt |
| `/myresult` | Déclarer le résultat de ton match |
| `/leaderboard` | Classement actuel |
| `/profile [membre]` | Ton rang, ta note, ton bilan et tes derniers matchs |

## Comprendre le classement

- Chaque joueur a une **note Glicko-2** (1500 au départ) et une incertitude.
- Les **4 premiers matchs** sont des matchs de placement : tu apparais à part du classement
  principal le temps de te calibrer.
- Battre un joueur mieux noté rapporte plus de points qu'une victoire attendue.
- **Un match par semaine.** Si le nombre de joueurs est impair, l'un d'eux est exempt.
- Les revanches récentes sont évitées (adversaire des 4 dernières semaines, puis 2).
- Après **3 semaines sans match**, un badge 💤 *inactif* apparaît. Ton incertitude remonte,
  ce qui te permet de retrouver rapidement ton niveau à ton retour.

## Bon à savoir

- Le classement principal est trié par note, du plus haut au plus bas.
- Un résultat devient définitif à la clôture de la semaine : en cas de désaccord, préviens
  un admin **avant** la clôture.
- `/profile` te montre ton meilleur rang et tes derniers résultats.
