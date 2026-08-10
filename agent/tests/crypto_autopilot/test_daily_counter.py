"""Tests for the persisted daily order counter.

Covers the restart-survival guarantee (the reason this module exists),
UTC day rollover, corrupt/missing file handling, and atomic writes.
"""

from __future__ import annotations

import json

import pytest

from src.crypto_autopilot.daily_counter import (
    COUNTER_FILENAME,
    DailyOrderCounter,
)

__all__ = []


@pytest.fixture
def counter(tmp_path) -> DailyOrderCounter:
    """A counter backed by a fresh temp runtime root."""
    return DailyOrderCounter(tmp_path)


# ---------------------------------------------------------------------------
# Fresh counter
# ---------------------------------------------------------------------------


class TestFreshCounter:
    def test_count_starts_at_zero(self, counter: DailyOrderCounter) -> None:
        """No file on disk → zero orders today."""
        assert counter.count_today() == 0

    def test_increment_persists_count(self, counter: DailyOrderCounter) -> None:
        """Increment returns the new count and survives re-instantiation."""
        assert counter.increment() == 1
        assert counter.increment() == 2

        # A brand-new instance reading the same file sees the same count.
        reloaded = DailyOrderCounter(counter.runtime_root)
        assert reloaded.count_today() == 2

    def test_increment_writes_json_payload(self, counter: DailyOrderCounter) -> None:
        """The file contains the expected {date, count} payload."""
        counter.increment()
        payload = json.loads(
            (counter.runtime_root / COUNTER_FILENAME).read_text(encoding="utf-8")
        )
        assert payload["count"] == 1
        assert len(payload["date"]) == 10  # YYYY-MM-DD


# ---------------------------------------------------------------------------
# UTC day rollover
# ---------------------------------------------------------------------------


class TestDayRollover:
    def test_old_date_reads_as_zero(self, counter: DailyOrderCounter, tmp_path) -> None:
        """A file dated yesterday is ignored — the daily cap restarts."""
        (tmp_path / COUNTER_FILENAME).write_text(
            json.dumps({"date": "2000-01-01", "count": 42}),
            encoding="utf-8",
        )
        assert counter.count_today() == 0

    def test_increment_overwrites_old_date(self, counter: DailyOrderCounter, tmp_path) -> None:
        """Incrementing after a rollover starts the new day at 1."""
        (tmp_path / COUNTER_FILENAME).write_text(
            json.dumps({"date": "2000-01-01", "count": 42}),
            encoding="utf-8",
        )
        assert counter.increment() == 1


# ---------------------------------------------------------------------------
# Corrupt / missing / negative payloads
# ---------------------------------------------------------------------------


class TestCorruptPayload:
    def test_invalid_json_reads_as_zero(self, counter: DailyOrderCounter, tmp_path) -> None:
        """Corrupt JSON degrades to zero instead of crashing the tick."""
        (tmp_path / COUNTER_FILENAME).write_text("{not json", encoding="utf-8")
        assert counter.count_today() == 0

    def test_non_int_count_reads_as_zero(self, counter: DailyOrderCounter, tmp_path) -> None:
        """A non-numeric count degrades to zero."""
        (tmp_path / COUNTER_FILENAME).write_text(
            json.dumps({"date": "2099-01-01", "count": "many"}),
            encoding="utf-8",
        )
        assert counter.count_today() == 0

    def test_negative_count_clamped_to_zero(self, counter: DailyOrderCounter, tmp_path) -> None:
        """A negative persisted count never makes the cap stricter."""
        (tmp_path / COUNTER_FILENAME).write_text(
            json.dumps({"date": "2099-01-01", "count": -5}),
            encoding="utf-8",
        )
        assert counter.count_today() == 0


# ---------------------------------------------------------------------------
# Integration: LiveExecutor.place_order feeds + updates the counter
# ---------------------------------------------------------------------------


class TestPlaceOrderCounter:
    @pytest.fixture
    def executor(self, tmp_path):
        """A LiveExecutor with a temp runtime root and fully mocked broker."""
        from src.crypto_autopilot.live_executor import LiveExecutor
        from src.crypto_autopilot.config import AutopilotConfig

        executor = LiveExecutor(
            config=AutopilotConfig(),
            runtime_root=tmp_path,
        )
        return executor

    def test_successful_order_increments_counter(
        self, executor, tmp_path, monkeypatch
    ) -> None:
        """An accepted order persists daily_count=1 for the next mandate check."""
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

        result = executor.place_order("BTC-USDT", "buy", 10.0)
        assert result["status"] == "ok"

        payload = json.loads(
            (tmp_path / COUNTER_FILENAME).read_text(encoding="utf-8")
        )
        assert payload["count"] == 1

    def test_rejected_order_does_not_increment(
        self, executor, tmp_path, monkeypatch
    ) -> None:
        """A mandate-rejected order leaves the counter untouched."""
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
        assert not (tmp_path / COUNTER_FILENAME).exists()


class _Breach:
    """Minimal stand-in for a mandate breach object."""

    limit = "max_trades_per_day"
    limit_value = 10
    attempted_value = 11
    kind = "quantitative"
    detail = "test breach"
