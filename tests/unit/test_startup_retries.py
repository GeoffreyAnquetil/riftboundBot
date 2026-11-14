from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import discord
import pytest

from riftbot.__main__ import (
    cloudflare_cooldown,
    describe_http_error,
    is_cloudflare_block,
    is_retryable_startup_error,
    retry_after_seconds,
    start_with_retries,
)


def http_error(
    status: int, headers: dict[str, str] | None = None, body: str = ""
) -> discord.HTTPException:
    response = SimpleNamespace(status=status, reason="test", headers=headers or {})
    return discord.HTTPException(response, body or {"message": "test", "code": 0})


class FakeBot:
    """Bot minimal : rejoue les erreurs fournies, puis se connecte."""

    def __init__(self, failures: list[BaseException]) -> None:
        self.failures = failures
        self.started = False

    async def __aenter__(self) -> FakeBot:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        return None

    async def start(self, token: str) -> None:
        if self.failures:
            raise self.failures.pop(0)
        self.started = True


class Factory:
    def __init__(self, failures: list[list[BaseException]]) -> None:
        self.failures = failures
        self.created: list[FakeBot] = []

    def __call__(self) -> FakeBot:
        bot = FakeBot(self.failures.pop(0))
        self.created.append(bot)
        return bot


async def no_sleep(delay: float) -> None:
    return None


def test_cloudflare_429_is_detected_by_the_missing_via_header() -> None:
    assert is_cloudflare_block(http_error(429)) is True
    assert is_cloudflare_block(http_error(429, {"Via": "1.1 google"})) is False
    assert is_cloudflare_block(http_error(500)) is False


def test_only_server_errors_and_api_rate_limits_are_retried() -> None:
    assert is_retryable_startup_error(http_error(500)) is True
    assert is_retryable_startup_error(http_error(503)) is True
    assert is_retryable_startup_error(http_error(429, {"Via": "1.1 google"})) is True
    # Un 429 sans Via est un blocage Cloudflare : insister l'aggrave.
    assert is_retryable_startup_error(http_error(429)) is False
    assert is_retryable_startup_error(discord.LoginFailure("bad token")) is False


def test_cloudflare_body_and_headers_are_reported() -> None:
    error = http_error(
        429,
        {"Content-Type": "text/html"},
        "<!DOCTYPE html><html><body>Error 1015: The owner of this website "
        "(discord.com) has banned you temporarily</body></html>",
    )

    details = describe_http_error(error)

    assert "Via=non" in details
    assert "Error 1015" in details
    assert "text/html" in details


def test_global_rate_limit_scope_is_reported() -> None:
    error = http_error(
        429,
        {
            "Via": "1.1 google",
            "X-RateLimit-Scope": "global",
            "Content-Type": "application/json",
            "Retry-After": "1.2",
        },
    )

    details = describe_http_error(error)

    assert "Via=oui" in details
    assert "scope=global" in details
    assert "Retry-After=1.2" in details


async def test_retry_after_is_read_from_cloudflare() -> None:
    assert retry_after_seconds(http_error(429, {"Retry-After": "65191"})) == 65191
    assert retry_after_seconds(http_error(429, {"Retry-After": "invalide"})) is None
    assert retry_after_seconds(http_error(429)) is None


def test_cooldown_never_shortens_cloudflare_request() -> None:
    # Observé en production : Cloudflare demande 18 h, la config 30 min.
    long_block = http_error(429, {"Retry-After": "65191"})

    cooldown = cloudflare_cooldown(long_block, configured=1800, maximum=86400)

    assert cooldown == timedelta(seconds=65191)


def test_cooldown_is_capped_by_the_maximum() -> None:
    very_long = http_error(429, {"Retry-After": "2000000"})

    assert cloudflare_cooldown(very_long, configured=0, maximum=86400) == timedelta(hours=24)


def test_cooldown_falls_back_on_the_configured_delay() -> None:
    no_header = http_error(429)

    assert cloudflare_cooldown(no_header, configured=1800, maximum=86400) == timedelta(minutes=30)
    # Une configuration à 0 laisse Cloudflare décider (ou 0 si rien n'est annoncé).
    assert cloudflare_cooldown(no_header, configured=0, maximum=86400) == timedelta(0)


async def test_transient_error_is_retried_with_a_growing_delay() -> None:
    factory = Factory([[http_error(503)], [http_error(502)], []])
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    await start_with_retries(
        factory,
        "token",
        attempts=3,
        initial_delay=10,
        max_delay=25,
        sleep=sleep,
    )

    assert [bot.started for bot in factory.created] == [False, False, True]
    # 10 puis 20 : le délai double, sans dépasser le plafond.
    assert delays == [10, 20]


async def test_success_on_the_first_attempt_does_not_restart_the_bot() -> None:
    factory = Factory([[]])

    await start_with_retries(
        factory,
        "token",
        attempts=5,
        initial_delay=10,
        max_delay=25,
        sleep=no_sleep,
    )

    assert len(factory.created) == 1
    assert factory.created[0].started is True


async def test_attempts_are_bounded() -> None:
    factory = Factory([[http_error(500)], [http_error(500)], [http_error(500)]])

    with pytest.raises(discord.HTTPException):
        await start_with_retries(
            factory,
            "token",
            attempts=3,
            initial_delay=0,
            max_delay=0,
            sleep=no_sleep,
        )

    assert len(factory.created) == 3


async def test_cloudflare_block_is_not_retried() -> None:
    factory = Factory([[http_error(429)]])

    with pytest.raises(discord.HTTPException):
        await start_with_retries(
            factory,
            "token",
            attempts=5,
            initial_delay=0,
            max_delay=0,
            sleep=no_sleep,
        )

    assert len(factory.created) == 1


async def test_permanent_error_is_not_retried() -> None:
    factory = Factory([[discord.LoginFailure("bad token")]])

    with pytest.raises(discord.LoginFailure):
        await start_with_retries(
            factory,
            "token",
            attempts=5,
            initial_delay=0,
            max_delay=0,
            sleep=no_sleep,
        )

    assert len(factory.created) == 1
