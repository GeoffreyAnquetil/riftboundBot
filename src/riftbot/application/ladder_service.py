"""Cas d'usage du ladder : inscription, semaines, résultats, classement."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import timedelta

from riftbot.application.dto import (
    LeaderboardEntry,
    MatchSummary,
    PlayerProfile,
    RankChange,
    ReportOutcome,
    WeekClosing,
    WeekOverview,
)
from riftbot.application.ports import Clock, EventBus, UnitOfWork, UnitOfWorkFactory
from riftbot.application.queries import require_match, summarize
from riftbot.domain.errors import (
    AlreadyRegistered,
    NoOpenWeek,
    NotEnoughPlayers,
    NotFound,
    NotRegistered,
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
from riftbot.domain.ladder.rules import LadderRule, Outcome

RECENT_MATCHES = 5


class LadderService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        rule: LadderRule,
        clock: Clock,
        events: EventBus,
        match_format: MatchFormat | None = None,
        auto_confirm_after: timedelta = timedelta(hours=24),
    ) -> None:
        self._uow = uow_factory
        self._rule = rule
        self._clock = clock
        self._events = events
        self._format = match_format or MatchFormat()
        self._auto_confirm_after = auto_confirm_after

    @property
    def match_format(self) -> MatchFormat:
        return self._format

    # --- Joueurs -----------------------------------------------------------------------------

    async def register(self, guild_id: int, discord_id: int, display_name: str) -> Player:
        """Ajoute un joueur en bas du classement (ou réactive un joueur retiré)."""
        async with self._uow() as uow:
            existing = await uow.players.get_by_discord_id(guild_id, discord_id)
            if existing is not None and existing.active:
                raise AlreadyRegistered(f"Tu fais déjà partie du ladder (#{existing.rank}).")

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
                existing.active, existing.rank, existing.display_name = True, rank, display_name
                await uow.players.update(existing)
                player = existing
            await uow.commit()
        return player

    async def remove_player(self, guild_id: int, discord_id: int) -> Player:
        """Retire un joueur : son match en cours est annulé et le classement est resserré."""
        async with self._uow() as uow:
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

            for rank, other in enumerate(await uow.players.list_active(guild_id), start=1):
                if other.rank != rank:
                    other.rank = rank
                    await uow.players.update(other)
            await uow.commit()

        for match in voided:
            await self._events.publish(MatchVoided(guild_id, match.pk))
        return player

    async def leaderboard(self, guild_id: int) -> list[LeaderboardEntry]:
        async with self._uow() as uow:
            players = await uow.players.list_active(guild_id)
            confirmed = await uow.matches.list_for_guild(guild_id, MatchStatus.CONFIRMED)
        wins, losses = _records(confirmed)
        return [LeaderboardEntry(p, wins[p.pk], losses[p.pk]) for p in players]

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
        return PlayerProfile(player, wins[player.pk], losses[player.pk], best_rank, recent)

    # --- Semaines ----------------------------------------------------------------------------

    async def open_week(self, guild_id: int) -> WeekOverview:
        """Ouvre une nouvelle semaine, photographie le classement et génère les matchs."""
        async with self._uow() as uow:
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
            matches = [
                await uow.matches.add(
                    Match(
                        guild_id=guild_id,
                        week_id=week.pk,
                        player1_id=pairing.higher,
                        player2_id=pairing.lower,
                    )
                )
                for pairing in self._rule.pair([p.pk for p in players], number)
            ]
            overview = WeekOverview(week, await summarize(uow, matches))
            await uow.commit()
        return overview

    async def week_overview(self, guild_id: int) -> WeekOverview | None:
        """Semaine en cours, ou à défaut la dernière clôturée."""
        async with self._uow() as uow:
            week = await uow.weeks.get_open(guild_id) or await uow.weeks.get_latest(guild_id)
            if week is None:
                return None
            matches = await uow.matches.list_for_week(week.pk)
            return WeekOverview(week, await summarize(uow, matches))

    async def close_week(self, guild_id: int) -> WeekClosing:
        """Clôture la semaine et applique la règle de ladder.

        - un résultat déclaré mais pas encore confirmé est validé ;
        - un match non joué est annulé (les paris sont remboursés) ;
        - un litige bloque la clôture : un admin doit d'abord trancher.
        """
        now = self._clock.now()
        confirmed: list[Match] = []
        voided: list[Match] = []
        async with self._uow() as uow:
            week = await self._require_open_week(uow, guild_id)
            matches = await uow.matches.list_for_week(week.pk)

            disputed = [m for m in matches if m.status is MatchStatus.DISPUTED]
            if disputed:
                ids = ", ".join(f"#{m.pk}" for m in disputed)
                raise UnresolvedDisputes(f"Litiges à trancher avant la clôture : {ids}.")

            for match in matches:
                if match.status is MatchStatus.REPORTED:
                    match.auto_confirm(now)
                    confirmed.append(match)
                elif match.status is MatchStatus.PENDING:
                    match.void(now)
                    voided.append(match)
                else:
                    continue
                await uow.matches.update(match)

            players = await uow.players.list_active(guild_id)
            by_id = {p.pk: p for p in players}
            outcomes = [Outcome(m.player1_id, m.player2_id, m.winner_id) for m in matches]
            new_order = self._rule.apply([p.pk for p in players], outcomes)

            changes: list[RankChange] = []
            for new_rank, player_id in enumerate(new_order, start=1):
                player = by_id[player_id]
                changes.append(RankChange(player, player.rank, new_rank))
                if player.rank != new_rank:
                    player.rank = new_rank
                    await uow.players.update(player)

            week.close(now)
            await uow.weeks.update(week)
            await uow.commit()

        await self._publish_confirmed(confirmed)
        for match in voided:
            await self._events.publish(MatchVoided(guild_id, match.pk))
        return WeekClosing(week, changes, confirmed=len(confirmed), voided=len(voided))

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
                (m for m in await uow.matches.list_for_week(week.pk) if m.involves(player.pk)),
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
            status = match.report(
                player.pk, own_score, opponent_score, self._format, self._clock.now()
            )
            await uow.matches.update(match)
            summary = (await summarize(uow, [match]))[0]
            await uow.commit()
        if status is MatchStatus.CONFIRMED:
            await self._publish_confirmed([match])
        return ReportOutcome(summary, status)

    async def confirm_result(self, guild_id: int, match_id: int, discord_id: int) -> MatchSummary:
        async with self._uow() as uow:
            player = await self._require_player(uow, guild_id, discord_id)
            match = await require_match(uow, guild_id, match_id)
            match.confirm(player.pk, self._clock.now())
            await uow.matches.update(match)
            summary = (await summarize(uow, [match]))[0]
            await uow.commit()
        await self._publish_confirmed([match])
        return summary

    async def dispute_result(self, guild_id: int, match_id: int, discord_id: int) -> MatchSummary:
        async with self._uow() as uow:
            player = await self._require_player(uow, guild_id, discord_id)
            match = await require_match(uow, guild_id, match_id)
            match.dispute(player.pk)
            await uow.matches.update(match)
            summary = (await summarize(uow, [match]))[0]
            await uow.commit()
        return summary

    async def set_result(
        self, guild_id: int, match_id: int, score1: int, score2: int
    ) -> MatchSummary:
        """Décision d'un admin (litige, forfait, oubli de déclaration)."""
        async with self._uow() as uow:
            match = await require_match(uow, guild_id, match_id)
            match.resolve(score1, score2, self._format, self._clock.now())
            await uow.matches.update(match)
            summary = (await summarize(uow, [match]))[0]
            await uow.commit()
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
            for match in stale:
                match.auto_confirm(now)
                await uow.matches.update(match)
            await uow.commit()
        await self._publish_confirmed(stale)
        return stale

    # --- Interne -----------------------------------------------------------------------------

    async def _publish_confirmed(self, matches: Sequence[Match]) -> None:
        for match in matches:
            side = match.winner_side
            if side is not None:
                await self._events.publish(MatchConfirmed(match.guild_id, match.pk, side))

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


def _records(confirmed: Sequence[Match]) -> tuple[Counter[int], Counter[int]]:
    wins: Counter[int] = Counter()
    losses: Counter[int] = Counter()
    for match in confirmed:
        if match.winner_id is not None and match.loser_id is not None:
            wins[match.winner_id] += 1
            losses[match.loser_id] += 1
    return wins, losses
