"""Tests for the strategy review engine (Loop Engineering 闭环第一圈).

Covers: vs-backtest health, signal/data freshness, hypothesis auto
transitions (testing→validated / testing→rejected / validated→monitoring),
recommendation levels, and fail-open behaviour.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.strategy.review_engine import (
    MIN_TRADES,
    StrategyReview,
    compute_review,
    _consecutive_losses,
    _reconstruct_nav,
)

__all__ = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _make_state(*, nav: float = 1.1, trades: list[dict] | None = None, started_at: str | None = "2026-07-01", last_signal: str | None = None) -> dict:
    return {
        "nav": nav,
        "started_at": started_at,
        "last_signal_date": last_signal or _now_iso()[:10],
        "trades": trades or [],
    }


def _make_metrics(*, annual: float = 12.77, max_dd: float = -10.62) -> dict:
    return {
        "updated_at": _now_iso(),
        "backtest": {"COMBO2(BAB+52w)": {"annual": annual, "max_dd": max_dd}},
    }


def _write_hypotheses(path: Path, statuses: dict[str, str]) -> None:
    records = [
        {
            "hypothesis_id": hid,
            "title": f"假设 {hid}",
            "thesis": "test",
            "status": status,
            "invalidation_notes": "",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        for hid, status in statuses.items()
    ]
    path.write_text(json.dumps(records), encoding="utf-8")


def _read_hypotheses(path: Path) -> dict[str, str]:
    return {h["hypothesis_id"]: h["status"] for h in json.loads(path.read_text(encoding="utf-8"))}


def _winning_trades(n: int) -> list[dict]:
    return [{"from": f"2026-07-{i+1:02d}", "to": f"2026-07-{i+2:02d}", "ret": 0.5} for i in range(n)]


def _losing_trades(n: int) -> list[dict]:
    return [{"from": f"2026-07-{i+1:02d}", "to": f"2026-07-{i+2:02d}", "ret": -0.4} for i in range(n)]


def _dd_trades() -> list[dict]:
    """首笔 -20% 然后 19 笔 +0.5% — 净值从 1.0 掉到 ~0.8, 回撤 ≈20%."""
    trades = [{"from": "2026-07-01", "to": "2026-07-02", "ret": -20.0}]
    trades += [
        {"from": f"2026-07-{i+2:02d}", "to": f"2026-07-{i+3:02d}", "ret": 0.5}
        for i in range(1, MIN_TRADES)
    ]
    return trades


class TestVsBacktest:
    def test_sample_insufficient_no_conclusion(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        state.write_text(json.dumps(_make_state(trades=_winning_trades(3))), encoding="utf-8")
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics()), encoding="utf-8")

        review = compute_review(state, metrics, tmp_path / "hypotheses.json")

        assert review.vs_backtest.sample_sufficient is False
        assert review.vs_backtest.outperforming is None
        assert any("样本不足" in r.text for r in review.recommendations)

    def test_outperforming_with_sufficient_sample(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        state.write_text(
            json.dumps(_make_state(nav=1.5, trades=_winning_trades(MIN_TRADES))), encoding="utf-8"
        )
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics(annual=5.0)), encoding="utf-8")

        review = compute_review(state, metrics)

        assert review.vs_backtest.sample_sufficient is True
        assert review.vs_backtest.outperforming is True
        assert any("跑赢回测" in r.text for r in review.recommendations)

    def test_dd_breach_detected(self, tmp_path: Path) -> None:
        # nav≈0.8, 回撤 ≈20% > 10.62*1.5 = 15.93 → breach
        state = tmp_path / "state.json"
        state.write_text(json.dumps(_make_state(nav=0.8, trades=_dd_trades())), encoding="utf-8")
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics(max_dd=-10.62)), encoding="utf-8")

        review = compute_review(state, metrics)

        assert review.vs_backtest.dd_breach is True
        assert any(r.level == "critical" for r in review.recommendations)


class TestHypothesisTransitions:
    def test_testing_outperform_becomes_validated(self, tmp_path: Path) -> None:
        hypo_path = tmp_path / "hypotheses.json"
        _write_hypotheses(hypo_path, {"hyp_1": "testing"})
        state = tmp_path / "state.json"
        state.write_text(
            json.dumps(_make_state(nav=1.5, trades=_winning_trades(MIN_TRADES))), encoding="utf-8"
        )
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics(annual=5.0)), encoding="utf-8")

        review = compute_review(state, metrics, hypo_path)

        assert _read_hypotheses(hypo_path)["hyp_1"] == "validated"
        assert review.hypothesis_updates[0].to_status == "validated"
        assert "跑赢" in review.hypothesis_updates[0].reason

    def test_testing_three_losses_becomes_rejected(self, tmp_path: Path) -> None:
        hypo_path = tmp_path / "hypotheses.json"
        _write_hypotheses(hypo_path, {"hyp_2": "testing"})
        state = tmp_path / "state.json"
        state.write_text(
            json.dumps(_make_state(nav=0.9, trades=_losing_trades(3))), encoding="utf-8"
        )
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics()), encoding="utf-8")

        review = compute_review(state, metrics, hypo_path)

        assert _read_hypotheses(hypo_path)["hyp_2"] == "rejected"
        assert "连续 3 笔亏损" in review.hypothesis_updates[0].reason

    def test_validated_dd_breach_downgraded_to_monitoring(self, tmp_path: Path) -> None:
        hypo_path = tmp_path / "hypotheses.json"
        _write_hypotheses(hypo_path, {"hyp_3": "validated"})
        state = tmp_path / "state.json"
        state.write_text(json.dumps(_make_state(nav=0.8, trades=_dd_trades())), encoding="utf-8")
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics(max_dd=-10.62)), encoding="utf-8")

        review = compute_review(state, metrics, hypo_path)

        assert _read_hypotheses(hypo_path)["hyp_3"] == "monitoring"
        assert review.hypothesis_updates[0].to_status == "monitoring"

    def test_idempotent_no_double_transition(self, tmp_path: Path) -> None:
        hypo_path = tmp_path / "hypotheses.json"
        _write_hypotheses(hypo_path, {"hyp_4": "testing"})
        state = tmp_path / "state.json"
        state.write_text(
            json.dumps(_make_state(nav=1.5, trades=_winning_trades(MIN_TRADES))), encoding="utf-8"
        )
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics(annual=5.0)), encoding="utf-8")

        first = compute_review(state, metrics, hypo_path)
        second = compute_review(state, metrics, hypo_path)

        assert len(first.hypothesis_updates) == 1
        assert len(second.hypothesis_updates) == 0  # 已 validated, 不再匹配 testing 规则

    def test_exploring_hypothesis_untouched(self, tmp_path: Path) -> None:
        hypo_path = tmp_path / "hypotheses.json"
        _write_hypotheses(hypo_path, {"hyp_5": "exploring"})
        state = tmp_path / "state.json"
        state.write_text(
            json.dumps(_make_state(nav=1.5, trades=_winning_trades(MIN_TRADES))), encoding="utf-8"
        )
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics(annual=5.0)), encoding="utf-8")

        compute_review(state, metrics, hypo_path)

        assert _read_hypotheses(hypo_path)["hyp_5"] == "exploring"


class TestFreshnessAndFailOpen:
    def test_stale_signal_and_metrics(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        state.write_text(
            json.dumps(_make_state(trades=_winning_trades(3), last_signal=_days_ago(5))),
            encoding="utf-8",
        )
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(
            json.dumps({**_make_metrics(), "updated_at": _days_ago(40)}), encoding="utf-8"
        )

        review = compute_review(state, metrics)

        assert review.signal_health.stale is True
        assert review.data_freshness.stale is True
        assert any("cron" in r.text for r in review.recommendations)
        assert any("重跑 combo_backtest" in r.text for r in review.recommendations)

    def test_fail_open_missing_files(self, tmp_path: Path) -> None:
        review = compute_review(tmp_path / "nope.json", tmp_path / "nope2.json", tmp_path / "nope3.json")

        assert review.vs_backtest.sample_sufficient is False
        assert review.signal_health.stale is False
        assert review.data_freshness.stale is False
        assert review.hypothesis_updates == []


class TestHelpers:
    def test_consecutive_losses(self) -> None:
        trades = _winning_trades(2) + _losing_trades(3)
        assert _consecutive_losses(trades) == 3
        assert _consecutive_losses(_winning_trades(4)) == 0

    def test_reconstruct_nav_roundtrip(self) -> None:
        trades = [{"from": "a", "to": "b", "ret": 10.0}, {"from": "b", "to": "c", "ret": -5.0}]
        navs = _reconstruct_nav(trades, 1.1 * 0.95)
        # 最后一段: 1.1 → *0.95 = 1.045; 反推: 1.045/0.95 = 1.1; 1.1/1.1 = 1.0
        assert abs(navs[0] - 1.0) < 1e-9
        assert abs(navs[1] - 1.1) < 1e-9
        assert abs(navs[2] - 1.045) < 1e-9


class TestAdaptations:
    """第三圈: 参数自适应规则."""

    def test_dd_breach_halves_exposure(self) -> None:
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        review = StrategyReview(
            vs_backtest=ReviewVsBacktest(dd_breach=True, current_dd=20.0, backtest_max_dd=-10.62)
        )
        adaptations = compute_adaptations(review, {"exposure_multiplier": 1.0})

        assert len(adaptations) == 1
        assert adaptations[0].param == "exposure_multiplier"
        assert adaptations[0].from_value == 1.0
        assert adaptations[0].to_value == 0.5

    def test_consecutive_losses_halves_exposure(self) -> None:
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        review = StrategyReview(
            vs_backtest=ReviewVsBacktest(consecutive_losses=3, sample_sufficient=True)
        )
        adaptations = compute_adaptations(review, {"exposure_multiplier": 0.5})

        assert adaptations[0].from_value == 0.5
        assert adaptations[0].to_value == 0.25  # 0.5*0.5

    def test_exposure_floor(self) -> None:
        from src.strategy.review_engine import (
            EXPOSURE_MIN,
            ReviewVsBacktest,
            compute_adaptations,
        )

        review = StrategyReview(
            vs_backtest=ReviewVsBacktest(dd_breach=True, current_dd=30.0, backtest_max_dd=-10.0)
        )
        adaptations = compute_adaptations(review, {"exposure_multiplier": EXPOSURE_MIN})

        assert adaptations == []  # 已在下限, 不再降

    def test_outperforming_recovers_exposure(self) -> None:
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        review = StrategyReview(
            vs_backtest=ReviewVsBacktest(
                sample_sufficient=True, outperforming=True, paper_trades=20
            )
        )
        adaptations = compute_adaptations(review, {"exposure_multiplier": 0.5})

        assert adaptations[0].from_value == 0.5
        assert adaptations[0].to_value == 0.6

    def test_no_adaptation_when_healthy(self) -> None:
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        review = StrategyReview(vs_backtest=ReviewVsBacktest(sample_sufficient=True))
        assert compute_adaptations(review, {"exposure_multiplier": 1.0}) == []

    def test_review_dict_includes_adaptations_and_variants(self) -> None:
        review = StrategyReview()
        d = review.to_dict()
        assert d["adaptations"] == []
        assert d["variants"] == []
        assert d["loop_next"] == "compose"

    def test_loop_next_research_on_dd_breach(self, tmp_path: Path) -> None:
        from src.strategy.review_engine import ReviewVsBacktest, compute_review

        state = tmp_path / "state.json"
        state.write_text(json.dumps(_make_state(nav=0.8, trades=_dd_trades())), encoding="utf-8")
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics(max_dd=-10.62)), encoding="utf-8")

        review = compute_review(state, metrics)

        assert review.loop_next == "research"

    def test_loop_next_compose_when_healthy(self, tmp_path: Path) -> None:
        state = tmp_path / "state.json"
        state.write_text(
            json.dumps(_make_state(nav=1.2, trades=_winning_trades(MIN_TRADES))), encoding="utf-8"
        )
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics(annual=8.0, max_dd=-10.0)), encoding="utf-8")

        review = compute_review(state, metrics)

        assert review.loop_next == "compose"
