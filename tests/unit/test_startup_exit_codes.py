from __future__ import annotations

from types import SimpleNamespace

import discord

from riftbot.__main__ import EXIT_CONFIG, EXIT_TRANSIENT, classify_startup_error


def http_error(status: int) -> discord.HTTPException:
    return discord.HTTPException(
        SimpleNamespace(status=status, reason="test"),
        {"message": "test", "code": 0},
    )


def test_invalid_token_is_a_permanent_configuration_error() -> None:
    assert classify_startup_error(discord.LoginFailure("bad token")) == EXIT_CONFIG


def test_forbidden_and_unauthorized_are_permanent() -> None:
    assert classify_startup_error(http_error(401)) == EXIT_CONFIG
    assert classify_startup_error(http_error(403)) == EXIT_CONFIG


def test_rate_limits_and_server_errors_are_transient() -> None:
    assert classify_startup_error(http_error(429)) == EXIT_TRANSIENT
    assert classify_startup_error(http_error(500)) == EXIT_TRANSIENT
    assert classify_startup_error(http_error(503)) == EXIT_TRANSIENT


def test_unknown_errors_are_transient() -> None:
    assert classify_startup_error(RuntimeError("db locked")) == EXIT_TRANSIENT
