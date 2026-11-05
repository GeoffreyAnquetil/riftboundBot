from __future__ import annotations

import pytest

from riftbot.discord_bot.views import result


def test_repeated_button_clicks_are_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un utilisateur ne doit pas pouvoir générer un appel API par clic."""
    result._BUTTON_LAST_USE.clear()
    now = 1000.0
    # Cible sous forme de chaîne : ``time`` est un détail d'implémentation du
    # module de vue, non réexporté.
    monkeypatch.setattr("riftbot.discord_bot.views.result.time.monotonic", lambda: now)

    try:
        assert not result._button_is_throttled(42)
        assert result._button_is_throttled(42)

        now += result.BUTTON_COOLDOWN_SECONDS
        assert not result._button_is_throttled(42)
    finally:
        result._BUTTON_LAST_USE.clear()
