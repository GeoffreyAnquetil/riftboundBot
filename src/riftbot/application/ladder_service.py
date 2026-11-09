"""Cas d'usage du ladder : inscription, semaines, résultats, classement.

Le ladder se joue une fois par semaine. Chaque semaine, un appariement évite les revanches
récentes puis, à la clôture, les notes Glicko-2 sont mises à jour en une seule passe. Les
joueurs peuvent s'inscrire à tout moment : s'ils arrivent pendant une semaine ouverte, le
service tente de leur trouver un adversaire encore libre (sans jamais dépasser un match par
semaine).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from riftbot.application.dto import (
    LeaderboardEntry,
    MatchSummary,
    PlayerProfile,
    RankChange,
    Registration,
    ReportFingerprint,
    ReportOutcome,
    WeekClosing,
    WeekOverview,
)
from riftbot.application.ports import Clock, EventBus, UnitOfWork, UnitOfWorkFactory
from riftbot.application.queries import require_match, summarize
from riftbot.domain.errors import (
    AlreadyRegistered,
    InvalidMatchState,
    NoOpenWeek,
    NotEnoughPlayers,
    NotFound,
    NotRegistered,
    StaleReport,
    UnresolvedDisputes,
    WeekAlreadyOpen,
)
from riftbot.domain.events import MatchConfirmed, MatchVoided
from riftbot.domain.ladder.models import (
    Match,
    MatchFormat,
    MatchStatus,
    Player,
    RankingSnapshot,
    Week,
)
from riftbot.domain.ladder.pairing import (
    PairingCandidate,
    PairKey,
    pair_key,
    pair_week,
)
from riftbot.domain.ladder.rating import Rating, update_match

RECENT_MATCHES = 5


class LadderService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        events: EventBus,
        match_format: MatchFormat | None = None,
        auto_confirm_after: timedelta = timedelta(hours=24),
        *,
        placement_matches: int = 4,
        rematch_preferred_weeks: int = 4,
        rematch_hard_weeks: int = 2,
        inactive_after_weeks: int = 3,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._events = events
        self._format = match_format or MatchFormat()
        self._auto_confirm_after = auto_confirm_after
        self._placement_matches = placement_matches
        self._rematch_preferred_weeks = rematch_preferred_weeks
        self._rematch_hard_weeks = rematch_hard_weeks
        self._inactive_after_weeks = inactive_after_weeks
        # Sérialise les opérations qui créent ou clôturent des matchs. Le bot est un
        # processus unique, mais deux interactions Discord peuvent arriver en parallèle et
        # tenter de créer un second match pour le même joueur.
        self._locks: dict[int, asyncio.Lock] = {}

    @property
    def match_format(self) -> MatchFormat:
        return self._format

    @property
    def placement_matches(self) -> int:
        return self._placement_matches

    @property
    def rematch_preferred_weeks(self) -> int:
        return self._rematch_preferred_weeks

    @property
    def rematch_hard_weeks(self) -> int:
        return self._rematch_hard_weeks

    @property
    def inactive_after_weeks(self) -> int:
        return self._inactive_after_weeks

    # --- Joueurs -----------------------------------------------------------------------------

    async def register(self, guild_id: int, discord_id: int, display_name: str) -> Registration:
        """Ajoute un joueur (ou réactive un ancien) et tente un match dans la semaine ouverte."""
        async with self._guard(guild_id), self._uow() as uow:
            existing = await uow.players.get_by_discord_id(guild_id, discord_id)
            if existing is not None and existing.active:
                raise AlreadyRegistered(f"Tu fais déjà partie du ladder (#{existing.rank}).")

            reactivated = existing is not None
            rank = len(await uow.players.list_active(guild_id)) + 1
            if existing is None:
                player = await uow.players.add(
                    Player(
                        guild_id=guild_id,
                        discord_id=discord_id,
                        display_name=display_name,
                        rank=rank,
                        registered_at=self._clock.now(),
                    )
                )
            else:
                # On conserve la note acquise : seul le temps d'absence augmente l'incertitude.
                if existing.inactive_weeks:
                    inflated = self._rating(existing).inflate(existing.inactive_weeks)
                    existing.rating_deviation = inflated.deviation
                existing.active = True
                existing.rank = rank
                existing.display_name = display_name
                existing.inactive_weeks = 0
                await uow.players.update(existing)
                player = existing

            players = await uow.players.list_active(guild_id)
            await self._assign_ranks(uow, players)

            match: MatchSummary | None = None
            week = await uow.weeks.get_open(guild_id)
            if week is not None:
                for summary in await self._pair_unmatched(uow, guild_id, week):
                    if summary.match.involves(player.pk):
                        match = summary
                        break
            await uow.commit()
            refreshed = await uow.players.get(player.pk)
            player = refreshed or player
        return Registration(player=player, match=match, reactivated=reactivated)

    async def remove_player(self, guild_id: int, discord_id: int) -> Player:
        """Retire un joueur, annule son match en cours et réapparie son adversaire si possible."""
        async with self._guard(guild_id), self._uow() as uow:
            player = await self._require_player(uow, guild_id, discord_id)
            player.active = False
            await uow.players.update(player)

            voided: list[Match] = []
            week = await uow.weeks.get_open(guild_id)
            if week is not None:
                for match in await uow.matches.list_for_week(week.pk):
                    if match.involves(player.pk) and not match.is_final:
                        match.void(self._clock.now())
                        await uow.matches.update(match)
                        voided.append(match)
                # L'adversaire est redevenu libre : on lui cherche un match s'il en existe un.
                await self._pair_unmatched(uow, guild_id, week)

            players = await uow.players.list_active(guild_id)
            await self._assign_ranks(uow, players)
            await uow.commit()

        for match in voided:
            await self._events.publish(MatchVoided(guild_id, match.pk))
        return player

    async def leaderboard(self, guild_id: int) -> list[LeaderboardEntry]:
        async with self._uow() as uow:
            players = await uow.players.list_active(guild_id)
            confirmed = await uow.matches.list_for_guild(guild_id, MatchStatus.CONFIRMED)
        wins, losses = _records(confirmed)
        return [
            LeaderboardEntry(
                player=p,
                wins=wins[p.pk],
                losses=losses[p.pk],
                provisional=p.rated_matches < self._placement_matches,
                inactive=p.inactive_weeks >= self._inactive_after_weeks,
                placement_matches=self._placement_matches,
            )
            for p in players
        ]

    async def profile(self, guild_id: int, discord_id: int) -> PlayerProfile:
        async with self._uow() as uow:
            player = await self._require_player(uow, guild_id, discord_id)
            matches = await uow.matches.list_for_player(player.pk)
            snapshots = await uow.snapshots.list_for_player(player.pk)
            confirmed = sorted(
                (m for m in matches if m.status is MatchStatus.CONFIRMED),
                key=lambda m: (m.resolved_at.timestamp() if m.resolved_at else 0.0, m.pk),
                reverse=True,
            )
            recent = await summarize(uow, confirmed[:RECENT_MATCHES])
        wins, losses = _records(confirmed)
        best_rank = min([player.rank, *(s.rank for s in snapshots)])
        return PlayerProfile(
            player=player,
            wins=wins[player.pk],
            losses=losses[player.pk],
            best_rank=best_rank,
            recent=recent,
            provisional=player.rated_matches < self._placement_matches,
            inactive=player.inactive_weeks >= self._inactive_after_weeks,
            placement_matches=self._placement_matches,
        )

    # --- Semaines ----------------------------------------------------------------------------

    async def open_week(self, guild_id: int) -> WeekOverview:
        """Ouvre une semaine, photographie le classement et génère les matchs."""
        async with self._guard(guild_id), self._uow() as uow:
            if await uow.weeks.get_open(guild_id) is not None:
                raise WeekAlreadyOpen("Une semaine est déjà en cours : clôture-la d'abord.")
            players = await uow.players.list_active(guild_id)
            if len(players) < 2:
                raise NotEnoughPlayers("Il faut au moins deux joueurs dans le ladder.")

            latest = await uow.weeks.get_latest(guild_id)
            number = 1 if latest is None else latest.number + 1
            week = await uow.weeks.add(
                Week(guild_id=guild_id, number=number, opened_at=self._clock.now())
            )
            await uow.snapshots.add_many(
                [RankingSnapshot(week_id=week.pk, player_id=p.pk, rank=p.rank) for p in players]
            )

            plan = pair_week(
                [self._candidate(p) for p in players],
                await self._history(uow, guild_id, number),
                preferred_weeks=self._rematch_preferred_weeks,
                hard_weeks=self._rematch_hard_weeks,
            )
            matches = [
                await uow.matches.add(
                    Match(
                        guild_id=guild_id,
                        week_id=week.pk,
                        player1_id=pairing.higher,
                        player2_id=pairing.lower,
                    )
                )
                for pairing in plan.pairings
            ]
            by_id = {p.pk: p for p in players}
            bye = by_id.get(plan.bye) if plan.bye is not None else None
            overview = WeekOverview(
                week=week,
                matches=await summarize(uow, matches),
                bye=bye,
                warnings=plan.warnings,
            )
            await uow.commit()
        return overview

    async def week_overview(self, guild_id: int) -> WeekOverview | None:
        """Semaine en cours, ou à défaut la dernière clôturée."""
        async with self._uow() as uow:
            week = await uow.weeks.get_open(guild_id) or await uow.weeks.get_latest(guild_id)
            if week is None:
                return None
            matches = await uow.matches.list_for_week(week.pk)
            players = await uow.players.list_active(guild_id)
            matched = {
                pid
                for m in matches
                if m.status is not MatchStatus.VOIDED
                for pid in (m.player1_id, m.player2_id)
            }
            unpaired = [p for p in players if p.pk not in matched]
            bye = unpaired[0] if week.is_open and len(unpaired) == 1 else None
            return WeekOverview(week, await summarize(uow, matches), bye=bye)

    async def close_week(self, guild_id: int) -> WeekClosing:
        """Clôture la semaine, valide les résultats en attente et met à jour les notes.

        - un résultat déclaré mais pas encore confirmé est validé ;
        - un match non joué est annulé ;
        - un litige bloque la clôture : un admin doit d'abord trancher.
        """
        now = self._clock.now()
        auto_confirmed: list[Match] = []
        voided: list[Match] = []
        async with self._guard(guild_id), self._uow() as uow:
            week = await self._require_open_week(uow, guild_id)
            matches = await uow.matches.list_for_week(week.pk)

            disputed = [m for m in matches if m.status is MatchStatus.DISPUTED]
            if disputed:
                ids = ", ".join(f"#{m.pk}" for m in disputed)
                raise UnresolvedDisputes(f"Litiges à trancher avant la clôture : {ids}.")

            for match in matches:
                if match.status is MatchStatus.REPORTED:
                    match.auto_confirm(now)
                    await uow.matches.update(match)
                    auto_confirmed.append(match)
                elif match.status is MatchStatus.PENDING:
                    match.void(now)
                    await uow.matches.update(match)
                    voided.append(match)

            players = await uow.players.list_active(guild_id)
            self._assert_one_match_per_player(players, matches)
            changes = await self._rate_week(uow, players, matches, now)

            week.close(now)
            await uow.weeks.update(week)
            await uow.commit()

        await self._publish_confirmed(auto_confirmed)
        for match in voided:
            await self._events.publish(MatchVoided(guild_id, match.pk))
        return WeekClosing(
            week=week,
            ranking=changes,
            confirmed=len(auto_confirmed),
            voided=len(voided),
        )

    # --- Matchs et résultats -----------------------------------------------------------------

    async def get_match(self, guild_id: int, match_id: int) -> MatchSummary:
        async with self._uow() as uow:
            match = await require_match(uow, guild_id, match_id)
            return (await summarize(uow, [match]))[0]

    async def current_match(self, guild_id: int, discord_id: int) -> MatchSummary:
        """Match de la semaine en cours pour ce joueur."""
        async with self._uow() as uow:
            player = await self._require_player(uow, guild_id, discord_id)
            week = await self._require_open_week(uow, guild_id)
            match = next(
                (
                    m
                    for m in await uow.matches.list_for_week(week.pk)
                    if m.involves(player.pk) and m.status is not MatchStatus.VOIDED
                ),
                None,
            )
            if match is None:
                raise NotFound("Tu n'as pas de match cette semaine.")
            return (await summarize(uow, [match]))[0]

    async def report_result(
        self, guild_id: int, match_id: int, discord_id: int, own_score: int, opponent_score: int
    ) -> ReportOutcome:
        async with self._uow() as uow:
            player = await self._require_player(uow, guild_id, discord_id)
            match = await require_match(uow, guild_id, match_id)
            await self._require_open_week_of(uow, match)
            status = match.report(
                player.pk, own_score, opponent_score, self._format, self._clock.now()
            )
            await uow.matches.update(match)
            summary = (await summarize(uow, [match]))[0]
            await uow.commit()
        if status is MatchStatus.CONFIRMED:
            await self._publish_confirmed([match])
        return ReportOutcome(summary, status)

    async def confirm_result(
        self,
        guild_id: int,
        match_id: int,
        discord_id: int,
        *,
        expected: ReportFingerprint | None = None,
    ) -> MatchSummary:
        """Confirme le résultat courant.

        ``expected`` vient du ``custom_id`` d'un bouton : s'il ne correspond plus
        à la déclaration courante, on refuse le clic. C'est la protection contre
        un vieux bouton « Confirmer » qui validerait un score corrigé depuis.
        """
        async with self._uow() as uow:
            player = await self._require_player(uow, guild_id, discord_id)
            match = await require_match(uow, guild_id, match_id)
            await self._require_open_week_of(uow, match)
            self._require_current_report(match, expected)
            match.confirm(player.pk, self._clock.now())
            await uow.matches.update(match)
            summary = (await summarize(uow, [match]))[0]
            await uow.commit()
        await self._publish_confirmed([match])
        return summary

    async def dispute_result(
        self,
        guild_id: int,
        match_id: int,
        discord_id: int,
        *,
        expected: ReportFingerprint | None = None,
    ) -> MatchSummary:
        async with self._uow() as uow:
            player = await self._require_player(uow, guild_id, discord_id)
            match = await require_match(uow, guild_id, match_id)
            await self._require_open_week_of(uow, match)
            # Même contrôle que pour la confirmation : un bouton obsolète ne
            # doit pas contester silencieusement une nouvelle déclaration.
            self._require_current_report(match, expected)
            match.dispute(player.pk)
            await uow.matches.update(match)
            summary = (await summarize(uow, [match]))[0]
            await uow.commit()
        return summary

    async def set_result(
        self, guild_id: int, match_id: int, score1: int, score2: int
    ) -> MatchSummary:
        """Décision d'un admin (litige, forfait, oubli, correction après clôture).

        Sur une semaine ouverte, le résultat est fixé puis noté à la clôture. Sur une
        semaine déjà clôturée, seule une correction de score à vainqueur constant est
        acceptée : les notes Glicko-2 ne dépendent que du vainqueur, donc elles restent
        valides. Changer le vainqueur exigerait de recalculer la semaine, ce qui n'est
        pas supporté en V1.
        """
        async with self._uow() as uow:
            match = await require_match(uow, guild_id, match_id)
            week = await uow.weeks.get(match.week_id)
            if week is None:
                raise NotFound(f"Le match #{match_id} n'a pas de semaine.")
            if week.is_open:
                match.resolve(score1, score2, self._format, self._clock.now())
            else:
                match.correct_score(score1, score2, self._format)
            await uow.matches.update(match)
            summary = (await summarize(uow, [match]))[0]
            await uow.commit()
        if week.is_open:
            await self._publish_confirmed([match])
        return summary

    async def auto_confirm_stale(self) -> list[Match]:
        """Valide les résultats déclarés sans réponse de l'adversaire depuis trop longtemps."""
        now = self._clock.now()
        cutoff = now - self._auto_confirm_after
        async with self._uow() as uow:
            stale = [
                m
                for m in await uow.matches.list_by_status(MatchStatus.REPORTED)
                if m.reported_at is not None and m.reported_at <= cutoff
            ]
            confirmed: list[Match] = []
            for match in stale:
                week = await uow.weeks.get(match.week_id)
                if week is None or not week.is_open:
                    continue
                match.auto_confirm(now)
                await uow.matches.update(match)
                confirmed.append(match)
            await uow.commit()
        await self._publish_confirmed(confirmed)
        return confirmed

    # --- Appariement -------------------------------------------------------------------------

    async def _pair_unmatched(
        self, uow: UnitOfWork, guild_id: int, week: Week
    ) -> list[MatchSummary]:
        """Apparie les joueurs de la semaine qui n'ont pas encore de match.

        Utilisé à l'inscription et après le retrait d'un joueur. Les joueurs déjà appariés
        (même via un match annulé) sont exclus : un match annulé libère la place.
        """
        players = await uow.players.list_active(guild_id)
        matches = await uow.matches.list_for_week(week.pk)
        matched = {
            pid
            for match in matches
            if match.status is not MatchStatus.VOIDED
            for pid in (match.player1_id, match.player2_id)
        }
        unpaired = [p for p in players if p.pk not in matched]
        if len(unpaired) < 2:
            return []

        plan = pair_week(
            [self._candidate(p) for p in unpaired],
            await self._history(uow, guild_id, week.number),
            preferred_weeks=self._rematch_preferred_weeks,
            hard_weeks=self._rematch_hard_weeks,
        )
        created = [
            await uow.matches.add(
                Match(
                    guild_id=guild_id,
                    week_id=week.pk,
                    player1_id=pairing.higher,
                    player2_id=pairing.lower,
                )
            )
            for pairing in plan.pairings
        ]
        return await summarize(uow, created)

    async def _history(
        self, uow: UnitOfWork, guild_id: int, current_week_number: int
    ) -> dict[PairKey, int]:
        """Nombre de semaines depuis la dernière rencontre confirmée de chaque paire."""
        confirmed = await uow.matches.list_for_guild(guild_id, MatchStatus.CONFIRMED)
        weeks: dict[int, Week | None] = {}
        history: dict[PairKey, int] = {}
        for match in confirmed:
            if match.week_id not in weeks:
                weeks[match.week_id] = await uow.weeks.get(match.week_id)
            week = weeks[match.week_id]
            if week is None:
                continue
            since = current_week_number - week.number
            if since < 1:
                continue
            key = pair_key(match.player1_id, match.player2_id)
            previous = history.get(key)
            if previous is None or since < previous:
                history[key] = since
        return history

    # --- Notes et classement -----------------------------------------------------------------

    async def _rate_week(
        self,
        uow: UnitOfWork,
        players: Sequence[Player],
        matches: Sequence[Match],
        now: datetime,
    ) -> list[RankChange]:
        by_id = {p.pk: p for p in players}
        involved = {pid for match in matches for pid in (match.player1_id, match.player2_id)}
        missing = involved.difference(by_id)
        if missing:
            by_id.update(await uow.players.get_many(missing))

        old_ratings = {p.pk: p.rating for p in players}
        old_ranks = {p.pk: p.rank for p in players}
        played: set[int] = set()

        for match in matches:
            if match.status is not MatchStatus.CONFIRMED:
                continue
            if match.rated:
                # Déjà noté : on évite de le compter comme une semaine sans match.
                played.update((match.player1_id, match.player2_id))
                continue
            first_player = by_id[match.player1_id]
            second_player = by_id[match.player2_id]
            winner_id = match.winner_id
            if winner_id is None:
                raise InvalidMatchState(f"Le match #{match.pk} n'a pas de vainqueur.")
            first_rating, second_rating = update_match(
                self._rating(first_player),
                self._rating(second_player),
                1.0 if winner_id == first_player.pk else 0.0,
            )
            for player, new_rating in (
                (first_player, first_rating),
                (second_player, second_rating),
            ):
                player.rating = new_rating.mu
                player.rating_deviation = new_rating.deviation
                player.rating_volatility = new_rating.volatility
                player.rated_matches += 1
                player.inactive_weeks = 0
                player.last_match_at = now
                played.add(player.pk)
                await uow.players.update(player)
            match.rated = True
            await uow.matches.update(match)

        for player in players:
            if player.pk in played:
                continue
            inflated = self._rating(player).inflate()
            player.rating_deviation = inflated.deviation
            player.inactive_weeks += 1
            player.byes += 1
            await uow.players.update(player)

        await self._assign_ranks(uow, players)
        return [
            RankChange(
                player=player,
                old_rank=old_ranks[player.pk],
                new_rank=player.rank,
                old_rating=old_ratings[player.pk],
                new_rating=player.rating,
                provisional=player.rated_matches < self._placement_matches,
            )
            for player in sorted(players, key=lambda p: p.rank)
        ]

    async def _assign_ranks(self, uow: UnitOfWork, players: Sequence[Player]) -> None:
        """Attribue les places : joueurs classés d'abord, puis joueurs en placement."""
        ranked = sorted(
            (p for p in players if p.rated_matches >= self._placement_matches),
            key=lambda p: (-p.rating, p.rating_deviation, p.pk),
        )
        # ``rank`` sert de départage entre joueurs en placement : il est attribué à
        # l'inscription (ou à la réactivation) à la dernière place, ce qui garantit qu'un
        # nouvel arrivant ne passe pas devant les inscrits actuels à note égale.
        provisional = sorted(
            (p for p in players if p.rated_matches < self._placement_matches),
            key=lambda p: (-p.rating, -p.rated_matches, p.rank, p.pk),
        )
        for position, player in enumerate([*ranked, *provisional], start=1):
            if player.rank != position:
                player.rank = position
                await uow.players.update(player)

    def _assert_one_match_per_player(
        self, players: Sequence[Player], matches: Sequence[Match]
    ) -> None:
        counts: Counter[int] = Counter()
        for match in matches:
            if match.status is MatchStatus.VOIDED:
                continue
            counts[match.player1_id] += 1
            counts[match.player2_id] += 1
        duplicated = [player_id for player_id, count in counts.items() if count > 1]
        if duplicated:
            names = ", ".join(
                next(
                    (p.display_name for p in players if p.pk == player_id),
                    f"#{player_id}",
                )
                for player_id in duplicated
            )
            raise InvalidMatchState(
                f"Plusieurs matchs pour un même joueur cette semaine : {names}."
            )

    # --- Interne -----------------------------------------------------------------------------

    @asynccontextmanager
    async def _guard(self, guild_id: int) -> AsyncIterator[None]:
        """Verrou par serveur pour les opérations d'appariement et de clôture."""
        lock = self._locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            yield

    @staticmethod
    def _rating(player: Player) -> Rating:
        return Rating(
            mu=player.rating,
            deviation=player.rating_deviation,
            volatility=player.rating_volatility,
        )

    @staticmethod
    def _candidate(player: Player) -> PairingCandidate:
        return PairingCandidate(
            player_id=player.pk,
            rating=player.rating,
            rating_deviation=player.rating_deviation,
            byes=player.byes,
            rank=player.rank,
        )

    async def _publish_confirmed(self, matches: Sequence[Match]) -> None:
        for match in matches:
            side = match.winner_side
            if side is not None:
                await self._events.publish(MatchConfirmed(match.guild_id, match.pk, side))

    @staticmethod
    def _require_current_report(match: Match, expected: ReportFingerprint | None) -> None:
        """Refuse un bouton créé pour une autre déclaration que la courante.

        ``expected`` est ``None`` pour les appels internes (tests, outils) qui
        n'ont pas de bouton à vérifier.
        """
        if expected is None:
            return
        current = ReportFingerprint.of(match)
        if current != expected:
            raise StaleReport(
                "Le résultat déclaré a changé depuis l'affichage de ce message. "
                "Utilise le message le plus récent pour confirmer ou contester."
            )

    @staticmethod
    async def _require_player(uow: UnitOfWork, guild_id: int, discord_id: int) -> Player:
        player = await uow.players.get_by_discord_id(guild_id, discord_id)
        if player is None or not player.active:
            raise NotRegistered()
        return player

    @staticmethod
    async def _require_open_week(uow: UnitOfWork, guild_id: int) -> Week:
        week = await uow.weeks.get_open(guild_id)
        if week is None:
            raise NoOpenWeek()
        return week

    @staticmethod
    async def _require_open_week_of(uow: UnitOfWork, match: Match) -> None:
        week = await uow.weeks.get(match.week_id)
        if week is None or not week.is_open:
            raise InvalidMatchState("La semaine de ce match est clôturée.")


def _records(confirmed: Sequence[Match]) -> tuple[Counter[int], Counter[int]]:
    wins: Counter[int] = Counter()
    losses: Counter[int] = Counter()
    for match in confirmed:
        if match.winner_id is not None and match.loser_id is not None:
            wins[match.winner_id] += 1
            losses[match.loser_id] += 1
    return wins, losses
