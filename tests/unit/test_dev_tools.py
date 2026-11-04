from riftbot.discord_bot.cogs.dev import FAKE_NAMES, next_fake_players
from riftbot.discord_bot.common import is_fake, mention
from riftbot.discord_bot.views import embeds
from riftbot.domain.economy.models import Wallet
from riftbot.domain.ladder.models import Player
from tests.conftest import FakeClock

REAL_ID = 123456789012345678  # format d'un vrai snowflake Discord


def test_fake_players_follow_existing_ones_and_ignore_real_ids() -> None:
    assert next_fake_players([], 2) == [(1, "🤖 Alpha"), (2, "🤖 Bravo")]
    assert next_fake_players([REAL_ID, 1, 2], 1) == [(3, "🤖 Charlie")]


def test_fake_names_get_a_suffix_once_exhausted() -> None:
    [(discord_id, name)] = next_fake_players([len(FAKE_NAMES)], 1)
    assert (discord_id, name) == (len(FAKE_NAMES) + 1, "🤖 Alpha 2")


def test_fake_players_are_not_pinged() -> None:
    def player(discord_id: int) -> Player:
        return Player(
            guild_id=1, discord_id=discord_id, display_name="Nom", rank=1,
            registered_at=FakeClock().now(),
        )  # fmt: skip

    assert is_fake(1) and not is_fake(REAL_ID)
    assert mention(player(1)) == "**Nom**"
    assert mention(player(REAL_ID)) == f"<@{REAL_ID}>"

    wallets = [Wallet(id=1, guild_id=1, user_id=1, balance=10)]
    assert "**🤖 Alpha**" in (embeds.richest(wallets, {1: "🤖 Alpha"}).description or "")
