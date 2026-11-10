"""Vérifie le câblage Discord sans connexion : commandes déclarées, payloads valides, modale."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest
from pydantic import ValidationError

from riftbot.bootstrap import Services, build_services
from riftbot.config import Settings
from riftbot.discord_bot.bot import RiftBot
from riftbot.discord_bot.cogs.admin import AdminCog
from riftbot.discord_bot.cogs.ladder import LadderCog
from riftbot.discord_bot.views.correction import (
    corrected_announcement,
    correction_view,
    outcome_view,
)
from riftbot.discord_bot.views.result import ResultModal, confirmation_view
from riftbot.infrastructure.memory.uow import InMemoryStore, InMemoryUnitOfWork
from tests.conftest import GUILD

EXPECTED_COMMANDS = {
    "register",
    "leaderboard",
    "matches",
    "myresult",
    "profile",
    "admin",
}

BETTING_COMMANDS = {
    "bet",
    "balance",
    "richest",
    "mybets",
}


@pytest.fixture
def memory_services() -> Services:
    store = InMemoryStore()
    return build_services(Settings(_env_file=None), lambda: InMemoryUnitOfWork(store))


async def test_bot_channel_filter(memory_services: Services) -> None:
    bot = RiftBot(memory_services, bot_channel_id=123)
    response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
    wrong = cast(Any, SimpleNamespace(channel_id=456, channel=None, response=response))
    right = cast(Any, SimpleNamespace(channel_id=123, channel=None, response=response))

    assert await bot._check_bot_channel(wrong) is False
    assert response.send_message.await_count == 1
    assert await bot._check_bot_channel(right) is True


async def test_all_commands_are_registered(memory_services: Services) -> None:
    bot = RiftBot(memory_services)
    await bot.register_components()
    try:
        commands = {c.name: c for c in bot.tree.get_commands()}
        assert set(commands) == EXPECTED_COMMANDS

        payloads: list[dict[str, Any]] = [c.to_dict(bot.tree) for c in commands.values()]
        for payload in payloads:
            assert 1 <= len(payload["description"]) <= 100, payload["name"]

        admin = next(p for p in payloads if p["name"] == "admin")
        assert {o["name"] for o in admin["options"]} == {
            "week-open",
            "week-close",
            "set-result",
            "correct-result",
            "remove-player",
        }
        assert admin["default_member_permissions"] is not None
    finally:
        cog = bot.get_cog("LadderCog")
        assert isinstance(cog, LadderCog)
        cog.auto_confirm.cancel()


async def test_betting_commands_are_opt_in(memory_services: Services) -> None:
    bot = RiftBot(memory_services, enable_betting=True)
    await bot.register_components()
    try:
        commands = {c.name for c in bot.tree.get_commands()}
        assert commands == EXPECTED_COMMANDS | BETTING_COMMANDS
    finally:
        cog = bot.get_cog("LadderCog")
        assert isinstance(cog, LadderCog)
        cog.auto_confirm.cancel()


async def test_dev_commands_are_opt_in(memory_services: Services) -> None:
    bot = RiftBot(memory_services, dev_commands=True)
    await bot.register_components()
    try:
        dev = next(c for c in bot.tree.get_commands() if c.name == "dev")
        payload = dev.to_dict(bot.tree)
        assert {o["name"] for o in payload["options"]} == {
            "seed",
            "report-as",
            "answer-as",
            "simulate",
        }
        assert payload["default_member_permissions"] is not None
    finally:
        cog = bot.get_cog("LadderCog")
        assert isinstance(cog, LadderCog)
        cog.auto_confirm.cancel()


async def test_result_modal_offers_every_bo3_score(memory_services: Services) -> None:
    ladder = memory_services.ladder
    for discord_id in (1, 2):
        await ladder.register(GUILD, discord_id, f"joueur{discord_id}")
    await ladder.open_week(GUILD)
    summary = await ladder.current_match(GUILD, 1)

    modal = ResultModal(ladder, summary, reporter_id=1)

    assert modal.title == "Résultat contre joueur2"
    assert [o.value for o in modal.score.options] == ["2-0", "2-1", "1-2", "0-2"]

    # Les boutons n'existent qu'après une déclaration : ils embarquent le score
    # pour qu'un ancien message ne puisse pas confirmer une correction.
    await ladder.report_result(GUILD, summary.match.pk, 1, 2, 0)
    reported = (await ladder.get_match(GUILD, summary.match.pk)).match
    view = confirmation_view(reported)
    assert len(view.children) == 2
    # ``children`` est typé comme des Item génériques : on lit le bouton
    # sous-jacent via getattr pour garder mypy strict content.
    custom_ids = [getattr(getattr(item, "item", None), "custom_id", "") for item in view.children]
    assert all(str(reported.pk) in custom_id for custom_id in custom_ids)
    assert all("2:0" in custom_id for custom_id in custom_ids)


async def test_correction_view_lists_open_week_matches_and_outcomes(
    memory_services: Services,
) -> None:
    ladder = memory_services.ladder
    for discord_id in (1, 2, 3, 4):
        await ladder.register(GUILD, discord_id, f"joueur{discord_id}")
    overview = await ladder.open_week(GUILD)

    match_select = correction_view(ladder, overview.matches).children[0]
    assert isinstance(match_select, discord.ui.Select)
    assert [option.value for option in match_select.options] == [
        str(summary.match.pk) for summary in overview.matches
    ]

    outcome_select = outcome_view(ladder, overview.matches[0]).children[0]
    assert isinstance(outcome_select, discord.ui.Select)
    assert [option.value for option in outcome_select.options] == ["2:0", "2:1", "0:2", "1:2"]


async def test_corrected_announcement_mentions_both_players(
    memory_services: Services,
) -> None:
    ladder = memory_services.ladder
    alice, bob = 111111111111111111, 222222222222222222
    await ladder.register(GUILD, alice, "Alice")
    await ladder.register(GUILD, bob, "Bob")
    match_id = (await ladder.open_week(GUILD)).matches[0].match.pk
    await ladder.set_result(GUILD, match_id, 2, 0)

    text = corrected_announcement(await ladder.get_match(GUILD, match_id))

    assert f"<@{alice}>" in text
    assert f"<@{bob}>" in text


def test_admin_ids_are_parsed() -> None:
    settings = Settings(_env_file=None, admin_user_ids="123, 456 ,789")
    assert settings.admin_ids == frozenset({123, 456, 789})
    assert Settings(_env_file=None, admin_user_ids="").admin_ids == frozenset()

    with pytest.raises(ValidationError):
        Settings(_env_file=None, admin_user_ids="123,abc")


async def test_admin_whitelist_controls_the_check(memory_services: Services) -> None:
    bot = RiftBot(memory_services, admin_user_ids=frozenset({42}))
    await bot.register_components()
    try:
        cog = bot.get_cog("AdminCog")
        assert isinstance(cog, AdminCog)

        allowed = SimpleNamespace(user=SimpleNamespace(id=42))
        denied = SimpleNamespace(user=SimpleNamespace(id=7))
        assert await cog.interaction_check(allowed) is True  # type: ignore[arg-type]
        assert await cog.interaction_check(denied) is False  # type: ignore[arg-type]

        admin = next(c for c in bot.tree.get_commands() if c.name == "admin")
        # La liste blanche remplace la permission Discord : le groupe reste visible,
        # l'accès étant filtré par interaction_check.
        assert admin.to_dict(bot.tree)["default_member_permissions"] is None
    finally:
        ladder_cog = bot.get_cog("LadderCog")
        assert isinstance(ladder_cog, LadderCog)
        ladder_cog.auto_confirm.cancel()


async def test_admin_check_falls_back_to_manage_guild(memory_services: Services) -> None:
    bot = RiftBot(memory_services)
    await bot.register_components()
    try:
        cog = bot.get_cog("AdminCog")
        assert isinstance(cog, AdminCog)

        manager = SimpleNamespace(
            user=SimpleNamespace(id=1, guild_permissions=discord.Permissions(manage_guild=True))
        )
        member = SimpleNamespace(
            user=SimpleNamespace(id=2, guild_permissions=discord.Permissions.none())
        )
        assert await cog.interaction_check(manager) is True  # type: ignore[arg-type]
        assert await cog.interaction_check(member) is False  # type: ignore[arg-type]

        admin = next(c for c in bot.tree.get_commands() if c.name == "admin")
        assert admin.to_dict(bot.tree)["default_member_permissions"] == (
            discord.Permissions(manage_guild=True).value
        )
    finally:
        ladder_cog = bot.get_cog("LadderCog")
        assert isinstance(ladder_cog, LadderCog)
        ladder_cog.auto_confirm.cancel()
