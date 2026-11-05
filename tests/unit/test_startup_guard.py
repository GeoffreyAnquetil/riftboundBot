from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from riftbot.infrastructure.startup_guard import StartupGuard, StartupLoopDetected

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def test_allows_attempts_up_to_limit(tmp_path: Path) -> None:
    guard = StartupGuard(tmp_path / "startup.json", max_attempts=2, window=timedelta(minutes=10))

    guard.check_and_record(NOW)
    guard.check_and_record(NOW)

    with pytest.raises(StartupLoopDetected):
        guard.check_and_record(NOW)


def test_old_attempts_are_pruned(tmp_path: Path) -> None:
    guard = StartupGuard(tmp_path / "startup.json", max_attempts=2, window=timedelta(minutes=10))

    guard.check_and_record(NOW)
    guard.check_and_record(NOW)

    # Les deux tentatives sont hors de la fenêtre : on repart à zéro.
    later = NOW + timedelta(minutes=11)
    guard.check_and_record(later)
    guard.check_and_record(later)

    with pytest.raises(StartupLoopDetected):
        guard.check_and_record(later)


def test_corrupt_state_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "startup.json"
    path.write_text("not-json", encoding="utf-8")
    guard = StartupGuard(path, max_attempts=1, window=timedelta(minutes=10))

    guard.check_and_record(NOW)

    with pytest.raises(StartupLoopDetected):
        guard.check_and_record(NOW)


def test_disabled_guard_does_nothing(tmp_path: Path) -> None:
    path = tmp_path / "startup.json"
    guard = StartupGuard(path, max_attempts=0, window=timedelta(minutes=10))

    for _ in range(10):
        guard.check_and_record(NOW)

    assert not path.exists()
