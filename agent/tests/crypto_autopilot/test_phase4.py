"""Phase 4 tests: shadow execution, gap report, staged live scale-up.

Covers the live-switch hardening added in the enterprise roadmap:
- the paper-live gap report aggregation (price gap bps, fee diff, slippage);
- the staged live order scale ladder (initial $5, max $50, 7 clean days,
  halt gating, persistence);
- the live executor's shadow mode (every live fill mirrored into paper);
- the orchestrator's live order path sized by the scale ladder;
- the $500-account parameter calibration defaults.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.crypto_autopilot.config import AutopilotConfig
from src.crypto_autopilot.gap_report import build_gap_report
from src.crypto_autopilot.live_scale import (
    current_live_scale,
    load_live_scale,
    maybe_scale_up,
    next_tier,
)
from src.crypto_autopilot.trade_ledger import (
    append_slippage_record,
    write_trade_record,
)
from src.crypto_autopilot.types import FactorLifecycle

__all__ = []


@pytest.fixture
def runtime_root(tmp_path):
    return tmp_path


@pytest.fixture
def orchestrator(runtime_root, monkeypatch):
    """Construct an AutopilotOrchestrator with a temp runtime root."""
    monkeypatch.setattr(
        "src.crypto_autopilot.orchestrator._default_runtime_root",
        lambda: runtime_root,
    )
    from src.crypto_autopilot.orchestrator import AutopilotOrchestrator

    return AutopilotOrchestrator(config=AutopilotConfig())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _days_ago(days: int) -> str:
    """ISO-8601 timestamp *days* before now (UTC), kept inside the window."""
    ts = datetime.now(timezone.utc) - timedelta(days=days)
    return ts.replace(microsecond=0).isoformat()


def _seed_slippage(
    runtime_root: Path,
    *,
    days: int = 7,
    bps: float = 5.0,
    records_per_day: int = 3,
) -> None:
    """Write *days* of clean slippage measurements into the runtime root."""
    for day_offset in range(days):
        for i in range(records_per_day):
            append_slippage_record(
                runtime_root,
                symbol="BTC-USDT",
                signal_price=100.0,
                fill_price=100.0 + bps / 10_000.0 * 100.0,
                ts=_days_ago(day_offset) + f":{i:02d}",
            )


# ---------------------------------------------------------------------------
# config: $500 calibration
# ---------------------------------------------------------------------------


class TestCalibration:
    def test_defaults_calibrated_for_500_usd_account(self) -> None:
        config = AutopilotConfig()
        assert config.max_order_notional_usd == 25.0  # 5% of $500
        assert config.max_total_exposure_usd == 200.0
        assert config.max_trades_per_day == 10
        assert config.kill_loss_pct == 5.0  # $500 → -$25/day circuit breaker

    def test_live_scale_defaults(self) -> None:
        config = AutopilotConfig()
        assert config.live_order_scale == 5.0
        assert config.live_scale_max_usd == 50.0
        assert config.live_scale_up_days == 7
        assert config.live_scale_up_max_slippage_bps == 20.0
        assert config.live_shadow_enabled is True

    def test_scale_tiers_are_fixed(self) -> None:
        from src.crypto_autopilot.config import SCALE_TIERS

        assert SCALE_TIERS == (5.0, 10.0, 25.0, 50.0)


# ---------------------------------------------------------------------------
# gap_report
# ---------------------------------------------------------------------------


class TestGapReport:
    def test_aggregates_paper_live_gap(self, tmp_path: Path) -> None:
        write_trade_record(
            tmp_path, engine="paper", symbol="BTC-USDT", side="buy",
            notional=25.0, price=100.0, fee=0.02, alpha_id="alpha_gap_01",
            ts=_days_ago(1),
        )
        write_trade_record(
            tmp_path, engine="live", symbol="BTC-USDT", side="buy",
            notional=5.0, price=100.05, fee=0.004, alpha_id="alpha_gap_01",
            ts=_days_ago(1),
        )
        report = build_gap_report(tmp_path, days=7)
        entry = report["by_symbol"]["BTC-USDT"]
        assert entry["paper"]["count"] == 1
        assert entry["live"]["count"] == 1
        # paper 100.00 vs live 100.05 → paper 5 bps cheaper (negative gap).
        assert entry["price_gap_bps"] == pytest.approx(-5.0)
        assert entry["paper"]["total_fee"] == pytest.approx(0.02)
        assert report["by_factor"]["alpha_gap_01"]["price_gap_bps"] == pytest.approx(-5.0)

    def test_slippage_summary(self, tmp_path: Path) -> None:
        _seed_slippage(tmp_path, days=3, bps=10.0, records_per_day=2)
        report = build_gap_report(tmp_path, days=7)
        assert report["slippage"]["records"] == 6
        assert report["slippage"]["avg_bps"] == pytest.approx(10.0)
        assert report["slippage"]["max_bps"] == pytest.approx(10.0)

    def test_empty_runtime_degrades_gracefully(self, tmp_path: Path) -> None:
        report = build_gap_report(tmp_path, days=7)
        assert report["by_symbol"] == {}
        assert report["by_factor"] == {}
        assert report["slippage"]["records"] == 0
        assert report["slippage"]["avg_bps"] is None

    def test_window_filters_old_records(self, tmp_path: Path) -> None:
        write_trade_record(
            tmp_path, engine="paper", symbol="BTC-USDT", side="buy",
            notional=25.0, price=100.0, fee=0.02,
            ts=_days_ago(30),
        )
        report = build_gap_report(tmp_path, days=7)
        assert "BTC-USDT" not in report["by_symbol"]

    def test_single_engine_no_gap(self, tmp_path: Path) -> None:
        write_trade_record(
            tmp_path, engine="paper", symbol="ETH-USDT", side="buy",
            notional=25.0, price=100.0, fee=0.02, ts=_days_ago(1),
        )
        report = build_gap_report(tmp_path, days=7)
        entry = report["by_symbol"]["ETH-USDT"]
        assert entry["price_gap_bps"] is None
        assert entry["live"]["count"] == 0


# ---------------------------------------------------------------------------
# live_scale
# ---------------------------------------------------------------------------


class TestLiveScale:
    def test_initial_scale_is_lowest_tier(self, tmp_path: Path) -> None:
        state = load_live_scale(tmp_path, initial=5.0)
        assert state["scale"] == 5.0
        assert state["tier_index"] == 0
        assert current_live_scale(tmp_path, initial=5.0) == 5.0

    def test_next_tier_ladder(self) -> None:
        assert next_tier(5.0) == 10.0
        assert next_tier(10.0) == 25.0
        assert next_tier(25.0) == 50.0
        assert next_tier(50.0) is None

    def test_scale_up_after_seven_clean_days(self, tmp_path: Path) -> None:
        config = AutopilotConfig()
        _seed_slippage(tmp_path, days=7, bps=5.0)
        result = maybe_scale_up(tmp_path, config)
        assert result["scaled_up"] is True
        assert result["old_scale"] == 5.0
        assert result["scale"] == 10.0
        assert result["clean_days"] == 7
        # Persisted — survives a fresh load.
        assert current_live_scale(tmp_path, initial=5.0) == 10.0

    def test_scale_up_requires_consecutive_clean_days(
        self, tmp_path: Path,
    ) -> None:
        config = AutopilotConfig()
        _seed_slippage(tmp_path, days=7, bps=5.0)
        # One extreme day (yesterday) blows past the 20 bps threshold even
        # after averaging with that day's earlier clean measurements.
        append_slippage_record(
            tmp_path, symbol="BTC-USDT",
            signal_price=100.0, fill_price=110.0,  # 10 000 bps
            ts=_days_ago(1) + ":bad",
        )
        result = maybe_scale_up(tmp_path, config)
        assert result["scaled_up"] is False
        assert result["clean_days"] < 7
        assert current_live_scale(tmp_path, initial=5.0) == 5.0

    def test_scale_up_blocked_by_halt(self, tmp_path: Path) -> None:
        config = AutopilotConfig()
        _seed_slippage(tmp_path, days=7, bps=5.0)
        result = maybe_scale_up(tmp_path, config, halt_active=True)
        assert result["scaled_up"] is False
        assert result["reason"] == "halt active"
        assert current_live_scale(tmp_path, initial=5.0) == 5.0

    def test_scale_up_requires_enough_data(self, tmp_path: Path) -> None:
        config = AutopilotConfig()
        _seed_slippage(tmp_path, days=2, bps=5.0)
        result = maybe_scale_up(tmp_path, config)
        assert result["scaled_up"] is False
        assert "have 2" in result["reason"]

    def test_no_scale_up_above_max(self, tmp_path: Path) -> None:
        config = AutopilotConfig()
        _seed_slippage(tmp_path, days=7, bps=5.0)
        # Pre-seed the state at the top tier.
        from src.crypto_autopilot.live_scale import save_live_scale

        save_live_scale(
            tmp_path, scale=50.0, tier_index=3,
            since=_days_ago(1), last_scale_up=_days_ago(1),
        )
        result = maybe_scale_up(tmp_path, config)
        assert result["scaled_up"] is False
        assert "max" in result["reason"]
        assert current_live_scale(tmp_path, initial=5.0) == 50.0

    def test_corrupt_state_degrades_to_initial(self, tmp_path: Path) -> None:
        (tmp_path / "live_scale.json").write_text("{not json", encoding="utf-8")
        state = load_live_scale(tmp_path, initial=5.0)
        assert state["scale"] == 5.0


# ---------------------------------------------------------------------------
# LiveExecutor shadow mode
# ---------------------------------------------------------------------------


class TestShadowMode:
    @staticmethod
    def _executor(
        tmp_path: Path,
        monkeypatch,
        *,
        shadow: bool,
        paper=None,
        order_result: dict | None = None,
    ):
        from unittest.mock import MagicMock

        from src.crypto_autopilot.live_executor import LiveExecutor

        if order_result is None:
            order_result = {"status": "ok", "order_id": "live-1"}

        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.okx_sdk.place_order",
            lambda cfg, **kw: order_result,
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.okx_sdk.get_positions",
            lambda cfg: {"status": "ok", "positions": []},
        )
        monkeypatch.setattr(
            "src.crypto_autopilot.live_executor.okx_sdk.get_account_snapshot",
            lambda cfg: {"status": "ok", "account": {"total_equity": "500"}},
        )
        # Redirect the halt tree so the sentinel check stays in tmp_path.
        monkeypatch.setattr(
            Path, "home", classmethod(lambda cls: tmp_path), raising=False,
        )
        executor = LiveExecutor(
            config=AutopilotConfig(),
            runtime_root=tmp_path,
            paper_engine=paper,
            shadow_mode=shadow,
        )
        return executor

    def test_shadow_mode_mirrors_live_fill(self, tmp_path: Path, monkeypatch) -> None:
        from unittest.mock import MagicMock

        paper = MagicMock()
        paper.place_order.return_value = {"status": "ok"}
        executor = self._executor(tmp_path, monkeypatch, shadow=True, paper=paper)
        result = executor.place_order("BTC-USDT", "buy", 5.0)
        assert result["status"] == "ok"
        paper.place_order.assert_called_once_with(
            symbol="BTC-USDT", side="buy", notional=5.0,
        )

    def test_shadow_disabled_does_not_mirror(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        from unittest.mock import MagicMock

        paper = MagicMock()
        executor = self._executor(tmp_path, monkeypatch, shadow=False, paper=paper)
        result = executor.place_order("BTC-USDT", "buy", 5.0)
        assert result["status"] == "ok"
        paper.place_order.assert_not_called()

    def test_shadow_failure_never_blocks_live_fill(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        from unittest.mock import MagicMock

        paper = MagicMock()
        paper.place_order.side_effect = RuntimeError("broker down")
        executor = self._executor(tmp_path, monkeypatch, shadow=True, paper=paper)
        result = executor.place_order("BTC-USDT", "buy", 5.0)
        assert result["status"] == "ok"

    def test_rejected_order_does_not_mirror(self, tmp_path: Path, monkeypatch) -> None:
        from unittest.mock import MagicMock

        paper = MagicMock()
        executor = self._executor(
            tmp_path, monkeypatch, shadow=True, paper=paper,
            order_result={"status": "error", "error": "insufficient funds"},
        )
        result = executor.place_order("BTC-USDT", "buy", 5.0)
        assert result["status"] == "error"
        paper.place_order.assert_not_called()


# ---------------------------------------------------------------------------
# Orchestrator live order path
# ---------------------------------------------------------------------------


class TestLiveOrderPath:
    def test_live_factor_orders_through_live_executor(
        self, orchestrator, monkeypatch,
    ) -> None:
        """A LIVE_DEPLOYED factor places a live order at the scale ladder."""
        from unittest.mock import MagicMock

        from src.crypto_autopilot.types import FactorCandidate

        candidate = FactorCandidate(
            alpha_id="alpha_live_01",
            source_code="def compute(panel): return panel['close']",
            created_at=datetime.now(timezone.utc),
            meta={"screen_ic_mean": 0.02},
        )
        orchestrator._active_factors = [{
            "alpha_id": candidate.alpha_id,
            "lifecycle": FactorLifecycle.LIVE_DEPLOYED.value,
            "candidate": candidate,
        }]
        monkeypatch.setattr(orchestrator, "_factor_has_signal", lambda info: True)
        live = MagicMock()
        live.place_order.return_value = {"status": "ok"}
        monkeypatch.setattr(orchestrator, "_live_executor", live)
        paper = MagicMock()
        monkeypatch.setattr(orchestrator, "_paper_engine", paper)

        asyncio.run(orchestrator._tick_trade())

        live.place_order.assert_called_once()
        symbol, side, notional = live.place_order.call_args.kwargs[
            "symbol"], live.place_order.call_args.kwargs["side"], \
            live.place_order.call_args.kwargs["notional"]
        assert symbol == orchestrator.config.pairs[0]
        assert side == "buy"
        # Initial scale tier = $5, capped by exposure/factor count (1 factor).
        assert notional == pytest.approx(5.0)

    def test_live_order_uses_persisted_scale(self, orchestrator, monkeypatch) -> None:
        from unittest.mock import MagicMock

        from src.crypto_autopilot.live_scale import save_live_scale
        from src.crypto_autopilot.types import FactorCandidate

        save_live_scale(
            orchestrator.health.runtime_root, scale=25.0, tier_index=2,
        )
        candidate = FactorCandidate(
            alpha_id="alpha_live_02",
            source_code="def compute(panel): return panel['close']",
            created_at=datetime.now(timezone.utc),
            meta={"screen_ic_mean": 0.02},
        )
        orchestrator._active_factors = [{
            "alpha_id": candidate.alpha_id,
            "lifecycle": FactorLifecycle.LIVE_DEPLOYED.value,
            "candidate": candidate,
        }]
        monkeypatch.setattr(orchestrator, "_factor_has_signal", lambda info: True)
        live = MagicMock()
        live.place_order.return_value = {"status": "ok"}
        monkeypatch.setattr(orchestrator, "_live_executor", live)
        monkeypatch.setattr(orchestrator, "_paper_engine", MagicMock())

        asyncio.run(orchestrator._tick_trade())

        notional = live.place_order.call_args.kwargs["notional"]
        assert notional == pytest.approx(25.0)

    def test_live_factor_skipped_when_no_signal(
        self, orchestrator, monkeypatch,
    ) -> None:
        from unittest.mock import MagicMock

        from src.crypto_autopilot.types import FactorCandidate

        candidate = FactorCandidate(
            alpha_id="alpha_live_03",
            source_code="def compute(panel): return panel['close']",
            created_at=datetime.now(timezone.utc),
            meta={"screen_ic_mean": 0.02},
        )
        orchestrator._active_factors = [{
            "alpha_id": candidate.alpha_id,
            "lifecycle": FactorLifecycle.LIVE_DEPLOYED.value,
            "candidate": candidate,
        }]
        monkeypatch.setattr(orchestrator, "_factor_has_signal", lambda info: False)
        live = MagicMock()
        monkeypatch.setattr(orchestrator, "_live_executor", live)

        asyncio.run(orchestrator._tick_trade())

        live.place_order.assert_not_called()
