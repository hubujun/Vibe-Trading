"""Tests for the autopilot liveness watchdog.

Covers the alive → stale → recovered state machine and the outbox
notifications emitted on each transition. The watchdog never touches the
network — the heartbeat files are stubbed under a tmp root.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.api.autopilot_watchdog import AutopilotWatchdog
from src.crypto_autopilot.health import HealthMonitor

__all__ = []


def _outbox_files(root: Path) -> list[Path]:
    outbox = root / "notifications"
    if not outbox.is_dir():
        return []
    return sorted(outbox.glob("*.json"))


def _outbox_kinds(root: Path) -> list[str]:
    kinds = []
    for path in _outbox_files(root):
        payload = json.loads(path.read_text(encoding="utf-8"))
        kinds.append(payload.get("kind"))
    return kinds


class TestWatchdogStateMachine:
    def test_first_check_never_notifies(self, tmp_path: Path) -> None:
        """The initial poll only records state — no notifications."""
        watchdog = AutopilotWatchdog(
            runtime_root=tmp_path, poll_interval_s=5, stale_after_s=60,
        )
        watchdog._check_once()
        watchdog._check_once()
        assert _outbox_files(tmp_path) == []

    def test_alive_to_stale_emits_down(self, tmp_path: Path) -> None:
        """A fresh heartbeat followed by a stale one emits autopilot_down."""
        health = HealthMonitor(tmp_path)
        watchdog = AutopilotWatchdog(
            runtime_root=tmp_path, poll_interval_s=5, stale_after_s=60,
        )
        health.write_heartbeat(int(time.time() * 1000))
        watchdog._check_once()  # record alive
        assert watchdog._was_alive is True

        # Rewrite the heartbeat with an old timestamp → stale.
        stale_ms = int(time.time() * 1000) - 120_000
        health.write_heartbeat(stale_ms)
        watchdog._check_once()

        assert _outbox_kinds(tmp_path) == ["autopilot_down"]
        payload = json.loads(_outbox_files(tmp_path)[0].read_text(encoding="utf-8"))
        assert "stale" in payload["body"]

    def test_stale_to_alive_emits_recovered(self, tmp_path: Path) -> None:
        """A stale heartbeat followed by a fresh one emits crash_recovered."""
        health = HealthMonitor(tmp_path)
        watchdog = AutopilotWatchdog(
            runtime_root=tmp_path, poll_interval_s=5, stale_after_s=60,
        )
        stale_ms = int(time.time() * 1000) - 120_000
        health.write_heartbeat(stale_ms)
        watchdog._check_once()  # record stale
        assert watchdog._was_alive is False

        health.write_heartbeat(int(time.time() * 1000))
        watchdog._check_once()

        assert _outbox_kinds(tmp_path) == ["crash_recovered"]

    def test_missing_heartbeat_counts_as_stale(self, tmp_path: Path) -> None:
        """No heartbeat file at all is fail-closed: treated as stale."""
        watchdog = AutopilotWatchdog(
            runtime_root=tmp_path, poll_interval_s=5, stale_after_s=60,
        )
        watchdog._check_once()  # record stale (no heartbeat file)
        assert watchdog._was_alive is False

    def test_no_repeat_down_without_recovery(self, tmp_path: Path) -> None:
        """Staying stale emits exactly one notification, not one per poll."""
        health = HealthMonitor(tmp_path)
        watchdog = AutopilotWatchdog(
            runtime_root=tmp_path, poll_interval_s=5, stale_after_s=60,
        )
        health.write_heartbeat(int(time.time() * 1000))
        watchdog._check_once()  # alive
        stale_ms = int(time.time() * 1000) - 120_000
        health.write_heartbeat(stale_ms)
        watchdog._check_once()  # → down (notify once)
        watchdog._check_once()  # still stale → no repeat
        watchdog._check_once()
        assert _outbox_kinds(tmp_path) == ["autopilot_down"]
