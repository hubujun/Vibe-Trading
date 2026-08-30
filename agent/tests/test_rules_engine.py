"""规则引擎测试 — Vibe 实盘规则 (2026-08-30).

2026-08-30 拍板: 移除 TradingAgents-CN 老旧纪律 (周四五/23:00 平仓).
保留测试: 宏观事件静默 / 连续 3 笔亏损当日停 / 日内亏损熔断 / 状态持久化.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.crypto_autopilot.rules_engine import (
    RuleConfig,
    RuleState,
    evaluate,
)

TZ = ZoneInfo("Asia/Shanghai")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


class TestMacroSilence:
    def test_silence_window_blocks(self) -> None:
        events = [{"date": "2026-09-03", "time": "14:00", "title": "FOMC", "level": "B"}]
        # 13:58 (事件前 2 分钟) → 静默
        v = evaluate(now=_dt("2026-09-03T13:58:00"), events=events)
        assert v.can_trade is False
        assert "静默" in (v.reason or "")
        # 14:03 (事件后 3 分钟) → 静默
        v2 = evaluate(now=_dt("2026-09-03T14:03:00"), events=events)
        assert v2.can_trade is False

    def test_outside_window_trades(self) -> None:
        events = [{"date": "2026-09-03", "time": "14:00", "title": "FOMC", "level": "B"}]
        v = evaluate(now=_dt("2026-09-03T13:50:00"), events=events)
        assert v.can_trade is True

    def test_event_without_time_ignored(self) -> None:
        events = [{"date": "2026-09-03", "title": "无时间事件", "level": "B"}]
        v = evaluate(now=_dt("2026-09-03T14:00:00"), events=events)
        assert v.can_trade is True  # 无 time 字段 → 不静默 (退化为杠杆乘数)


class TestConsecutiveLosses:
    def test_three_losses_blocks(self) -> None:
        trades = [
            {"ts": "2026-09-03T09:00:00", "realized_pnl": -1.2},
            {"ts": "2026-09-03T10:00:00", "realized_pnl": -0.8},
            {"ts": "2026-09-03T11:00:00", "realized_pnl": -2.1},
        ]
        v = evaluate(now=_dt("2026-09-03T12:00:00"), closed_trades_today=trades)
        assert v.can_trade is False
        assert "连续" in (v.reason or "")

    def test_two_losses_then_win_allows(self) -> None:
        trades = [
            {"ts": "2026-09-03T09:00:00", "realized_pnl": -1.2},
            {"ts": "2026-09-03T10:00:00", "realized_pnl": -0.8},
            {"ts": "2026-09-03T11:00:00", "realized_pnl": 1.5},  # 盈利打断连亏
        ]
        v = evaluate(now=_dt("2026-09-03T12:00:00"), closed_trades_today=trades)
        assert v.can_trade is True

    def test_no_trades_allows(self) -> None:
        v = evaluate(now=_dt("2026-09-03T12:00:00"), closed_trades_today=[])
        assert v.can_trade is True


class TestDailyLossHalt:
    def test_loss_exceeds_threshold_halts(self) -> None:
        state = RuleState(day="2026-09-03", equity_baseline=1000.0)
        v = evaluate(
            now=_dt("2026-09-03T12:00:00"),
            state=state,
            equity_now=930.0,  # 亏 7% >= 5%
        )
        assert v.action == "halt"
        assert v.can_trade is False

    def test_loss_under_threshold_ok(self) -> None:
        state = RuleState(day="2026-09-03", equity_baseline=1000.0)
        v = evaluate(now=_dt("2026-09-03T12:00:00"), state=state, equity_now=980.0)
        assert v.action is None
        assert v.can_trade is True

    def test_no_baseline_skips(self) -> None:
        v = evaluate(now=_dt("2026-09-03T12:00:00"), equity_now=500.0)
        assert v.action is None  # 无基线 → 跳过熔断 (首次快照)


class TestStatePersistence:
    def test_roundtrip(self, tmp_path) -> None:
        p = tmp_path / "rule_state.json"
        st = RuleState(day="2026-09-03", equity_baseline=1000.0, consecutive_losses=2)
        st.save(p)
        loaded = RuleState.load(p)
        assert loaded.day == "2026-09-03"
        assert loaded.equity_baseline == 1000.0
        assert loaded.consecutive_losses == 2

    def test_load_missing_returns_default(self, tmp_path) -> None:
        st = RuleState.load(tmp_path / "nope.json")
        assert st.day == ""
        assert st.equity_baseline is None
        assert st.consecutive_losses == 0


class TestConfigOverride:
    def test_custom_loss_threshold(self) -> None:
        cfg = RuleConfig(daily_loss_pct=2.0)
        state = RuleState(day="2026-09-03", equity_baseline=1000.0)
        # 亏 2.5% >= 2% → 熔断
        v = evaluate(now=_dt("2026-09-03T12:00:00"), state=state, equity_now=975.0, cfg=cfg)
        assert v.action == "halt"
        # 亏 1.5% < 2% → 不熔断
        v2 = evaluate(now=_dt("2026-09-03T12:00:00"), state=state, equity_now=985.0, cfg=cfg)
        assert v2.action is None
