"""Tests for the autopilot IM-notification outbox.

Covers the notifier's best-effort file writes plus the wiring from
:class:`LiveExecutor` (order fills) and :class:`RiskMonitor` (halts) into
the outbox. The API-server worker that relays these files to the IM
channel bus is tested in ``tests/test_autopilot_notify.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.crypto_autopilot.notifier import NOTIFY_DIR, AutopilotNotifier

__all__ = []


@pytest.fixture
def notifier(tmp_path) -> AutopilotNotifier:
    """A notifier backed by a fresh temp runtime root."""
    return AutopilotNotifier(tmp_path)


# ---------------------------------------------------------------------------
# Outbox writes
# ---------------------------------------------------------------------------


class TestOutboxWrites:
    def test_notify_writes_json_payload(self, notifier, tmp_path) -> None:
        """The outbox file carries the full structured payload."""
        path = notifier.notify(
            "order_filled", "Order filled", "notional=10",
            meta={"symbol": "BTC-USDT", "side": "buy"},
        )
        assert path is not None
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["kind"] == "order_filled"
        assert payload["title"] == "Order filled"
        assert payload["body"] == "notional=10"
        assert payload["meta"] == {"symbol": "BTC-USDT", "side": "buy"}
        assert payload["id"]
        assert payload["created_at"]

    def test_notify_lands_in_notify_dir(self, notifier, tmp_path) -> None:
        """Files are written under ``<runtime_root>/notifications``."""
        path = notifier.notify("halt_tripped", "Halt", "reason")
        assert path.parent == tmp_path / NOTIFY_DIR
        assert path.suffix == ".json"

    def test_notify_leaves_no_temp_files(self, notifier) -> None:
        """Atomic-write temp files are cleaned up on success."""
        notifier.notify("factor_promoted", "P", "d")
        assert list(notifier._outbox.glob("*.tmp")) == []

    def test_notify_unknown_kind_still_persisted(self, notifier) -> None:
        """Future kinds never break the outbox (worker renders generic)."""
        path = notifier.notify("future_kind", "T", "B")
        assert path is not None

    def test_notify_failure_returns_none(self, tmp_path) -> None:
        """A write failure degrades to None instead of raising."""
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        n = AutopilotNotifier(blocker)
        assert n.notify("order_filled", "T", "B") is None


# ---------------------------------------------------------------------------
# Integration: LiveExecutor.place_order feeds the outbox
# ---------------------------------------------------------------------------


class TestPlaceOrderNotifies:
    @pytest.fixture
    def executor(self, tmp_path):
        """A LiveExecutor with a temp runtime root and fully mocked broker."""
        from src.crypto_autopilot.config import AutopilotConfig
        from src.crypto_autopilot.live_executor import LiveExecutor

        return LiveExecutor(config=AutopilotConfig(), runtime_root=tmp_path)

    def _mock_okx_success(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.halt_flag_set",
            lambda broker: False,
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.check_mandate",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.okx_sdk.get_positions",
            lambda config: {},
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.okx_sdk.get_account_snapshot",
            lambda config: {},
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.okx_sdk.place_order",
            lambda config, **k: {"status": "ok", "order_id": "t-1"},
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.write_live_action",
            lambda **k: None,
        )

    def test_successful_order_writes_outbox_file(
        self, executor, tmp_path, monkeypatch,
    ) -> None:
        """An accepted order emits one order_filled notification."""
        self._mock_okx_success(monkeypatch)
        result = executor.place_order("BTC-USDT", "buy", 10.0)
        assert result["status"] == "ok"

        files = list((tmp_path / NOTIFY_DIR).glob("*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["kind"] == "order_filled"
        assert payload["meta"] == {
            "symbol": "BTC-USDT", "side": "buy", "notional": 10.0,
        }

    def test_rejected_order_writes_no_notification(
        self, executor, tmp_path, monkeypatch,
    ) -> None:
        """A mandate-rejected order leaves the outbox untouched."""
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.halt_flag_set",
            lambda broker: False,
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.check_mandate",
            lambda *a, **k: _Breach(),
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.okx_sdk.get_positions",
            lambda config: {},
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.okx_sdk.get_account_snapshot",
            lambda config: {},
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.write_live_action",
            lambda **k: None,
        )

        result = executor.place_order("BTC-USDT", "buy", 10.0)
        assert result["status"] == "rejected"
        assert not (tmp_path / NOTIFY_DIR).exists()


# ---------------------------------------------------------------------------
# Integration: RiskMonitor.trigger_halt feeds the outbox
# ---------------------------------------------------------------------------


class TestTriggerHaltNotifies:
    def _monitor(self, tmp_path, monkeypatch):
        """A RiskMonitor whose outbox and sentinel resolve under tmp_path."""
        from src.crypto_autopilot.config import AutopilotConfig
        from src.crypto_autopilot.risk_monitor import RiskMonitor

        monkeypatch.setattr(
            "src.crypto_autopilot.risk_monitor._default_runtime_root",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.risk_monitor.trip_halt",
            lambda *a, **k: None,
        )
        return RiskMonitor(config=AutopilotConfig())

    def test_trigger_halt_writes_outbox_file(self, tmp_path, monkeypatch) -> None:
        """A successful trip emits one halt_tripped notification."""
        monitor = self._monitor(tmp_path, monkeypatch)
        monitor.trigger_halt("test reason")

        files = list((tmp_path / NOTIFY_DIR).glob("*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["kind"] == "halt_tripped"
        assert payload["meta"] == {"reason": "test reason", "broker": "okx"}

    def test_trip_failure_skips_notification(self, tmp_path, monkeypatch) -> None:
        """A failed sentinel write re-raises before any notify happens."""
        from src.crypto_autopilot.config import AutopilotConfig
        from src.crypto_autopilot.risk_monitor import RiskMonitor

        monkeypatch.setattr(
            "src.crypto_autopilot.risk_monitor._default_runtime_root",
            lambda: tmp_path,
        )

        def _boom(*a, **k):
            raise OSError("no")

        monkeypatch.setattr(
            "src.crypto_autopilot.risk_monitor.trip_halt", _boom,
        )
        monitor = RiskMonitor(config=AutopilotConfig())
        with pytest.raises(OSError):
            monitor.trigger_halt("x")
        assert not (tmp_path / NOTIFY_DIR).exists()


class _Breach:
    """Minimal stand-in for a mandate breach object."""

    limit = "max_trades_per_day"
    limit_value = 10
    attempted_value = 11
    kind = "quantitative"
    detail = "test breach"
