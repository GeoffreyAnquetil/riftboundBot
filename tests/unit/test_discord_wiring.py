"""Vérifie le câblage Discord sans connexion : commandes déclarées, payloads valides, modale."""

from __future__ import annotations

from typing import Any

import pytest

from riftbot.bootstrap import Services, build_services
from riftbot.config import Settings
from riftbot.discord_bot.bot import RiftBot
from riftbot.discord_bot.cogs.ladder import LadderCog
from riftbot.discord_bot.views.result import ResultModal, confirmation_view
from riftbot.infrastructure.memory.uow import InMemoryStore, InMemoryUnitOfWork
from tests.conftest import GUILD

EXPECTED_COMMANDS = {
    "register",
    "leaderboard",
    "matches",
    "myresult",
    "profile",
    "bet",
    "balance",
    "richest",
    "mybets",
    "admin",
}


@pytest.fixture
def memory_services() -> Services:
    store = InMemoryStore()
    return build_services(Settings(_env_file=None), lambda: InMemoryUnitOfWork(store))


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
            "remove-player",
        }
        assert admin["default_member_permissions"] is not None
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
            "bet-as",
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
