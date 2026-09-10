"""Tests for the strategy review engine (Loop Engineering 闭环第一圈).

Covers: vs-backtest health, signal/data freshness, hypothesis auto
transitions (testing→monitoring on losing streak / testing|monitoring→validated /
validated→monitoring on dd-breach / rejected→monitoring recovery),
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
    _apply_hypothesis_rule,
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


def _write_hypotheses_sd(path: Path, entries: list[tuple[str, str, str]]) -> None:
    """写假设库, 带 signal_definition — entries = [(hid, status, signal_definition)]."""
    records = [
        {
            "hypothesis_id": hid,
            "title": f"假设 {hid}",
            "thesis": "test",
            "status": status,
            "signal_definition": sd,
            "invalidation_notes": "",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        for hid, status, sd in entries
    ]
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


def _setup_own_strategy(
    tmp_path: Path,
    monkeypatch,
    sd: str,
    *,
    nav: float,
    n: int,
    phase: str = "paper",
) -> None:
    """给假设铺一条自己的模拟盘策略 (strategies.json + state.json), 并把 HOME 指到 tmp.

    晋升/恢复规则都要求"假设自己的策略", 所以这些测试必须提供 strategies.json
    (``_strategy_for_sd`` 读 ``Path.home()/.vibe-trading/workbench/strategies.json``)。
    """
    run_dir = tmp_path / "runs" / "paper_own"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(
        json.dumps({"nav": nav, "trades": [{"ret": 0.5} for _ in range(n)]}), encoding="utf-8"
    )
    wb = tmp_path / ".vibe-trading" / "workbench"
    wb.mkdir(parents=True, exist_ok=True)
    (wb / "strategies.json").write_text(
        json.dumps(
            {
                "strategies": [
                    {
                        "strategy_id": "combo_own",
                        "signal_definition": sd,
                        "phase": phase,
                        "run_dir": str(run_dir),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


def _derisk_history(days_ago: int) -> list[dict]:
    """构造一条"days_ago 天前降过杠杆"的 adaptation_history (供 dwell 判定测试)."""
    at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return [
        {
            "param": "exposure_multiplier",
            "from_value": 1.0,
            "to_value": 0.25,
            "reason": "连续 6 笔亏损 → 仓位降到 0.25",
            "at": at,
        }
    ]


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
    def test_testing_outperform_becomes_validated(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """晋升要求假设自己的策略达标 (自身样本+净值), 不能跟着基策略一起晋升."""
        sd = "sd_1"
        hypo_path = tmp_path / "hypotheses.json"
        _write_hypotheses_sd(hypo_path, [("hyp_1", "testing", sd)])
        _setup_own_strategy(tmp_path, monkeypatch, sd, nav=1.5, n=MIN_TRADES)
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

    def test_no_own_strategy_never_promoted(self, tmp_path: Path, monkeypatch) -> None:
        """回归: 定时任务里 vs 是基策略的对照 —— 没自己策略的假设不该被一起晋升."""
        hypo_path = tmp_path / "hypotheses.json"
        _write_hypotheses_sd(hypo_path, [("hyp_6", "testing", "sd_absent")])
        _setup_own_strategy(tmp_path, monkeypatch, "sd_other", nav=1.5, n=MIN_TRADES)
        state = tmp_path / "state.json"
        state.write_text(
            json.dumps(_make_state(nav=1.5, trades=_winning_trades(MIN_TRADES))), encoding="utf-8"
        )
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics(annual=5.0)), encoding="utf-8")

        review = compute_review(state, metrics, hypo_path)

        assert _read_hypotheses(hypo_path)["hyp_6"] == "testing"
        assert review.hypothesis_updates == []

    def test_testing_three_losses_becomes_monitoring(self, tmp_path: Path) -> None:
        """2026-09-10 改口径: 连亏 3 笔只降级观察, 不再永久否决."""
        hypo_path = tmp_path / "hypotheses.json"
        _write_hypotheses(hypo_path, {"hyp_2": "testing"})
        state = tmp_path / "state.json"
        state.write_text(
            json.dumps(_make_state(nav=0.9, trades=_losing_trades(3))), encoding="utf-8"
        )
        metrics = tmp_path / "backtest_metrics.json"
        metrics.write_text(json.dumps(_make_metrics()), encoding="utf-8")

        review = compute_review(state, metrics, hypo_path)

        assert _read_hypotheses(hypo_path)["hyp_2"] == "monitoring"
        assert review.hypothesis_updates[0].to_status == "monitoring"
        assert "连续 3 笔亏损" in review.hypothesis_updates[0].reason
        assert "未否决" in review.hypothesis_updates[0].reason

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

    def test_idempotent_no_double_transition(self, tmp_path: Path, monkeypatch) -> None:
        sd = "sd_4"
        hypo_path = tmp_path / "hypotheses.json"
        _write_hypotheses_sd(hypo_path, [("hyp_4", "testing", sd)])
        _setup_own_strategy(tmp_path, monkeypatch, sd, nav=1.5, n=MIN_TRADES)
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


class TestHypothesisRuleBranches:
    """_apply_hypothesis_rule 各分支 (2026-09-10 口径调整 + 恢复路径)."""

    @staticmethod
    def _hyp(status: str):
        from types import SimpleNamespace

        return SimpleNamespace(hypothesis_id="hyp_x", title="假设 X", status=status)

    @staticmethod
    def _vs(**kwargs):
        from src.strategy.review_engine import ReviewVsBacktest

        return ReviewVsBacktest(**kwargs)

    def test_streak_downgrades_to_monitoring_not_rejected(self) -> None:
        upd = _apply_hypothesis_rule(self._hyp("testing"), _losing_trades(3), self._vs())
        assert upd is not None
        assert upd.to_status == "monitoring"
        assert upd.from_status == "testing"

    def test_rejected_recovers_when_sample_insufficient(self) -> None:
        """rejected 且自己的策略样本 < 门槛 → 回 monitoring (撤销证据不足的否决)."""
        upd = _apply_hypothesis_rule(
            self._hyp("rejected"), _losing_trades(10), self._vs(), has_own_state=True
        )
        assert upd is not None
        assert upd.to_status == "monitoring"
        assert "不足以下否决结论" in upd.reason

    def test_rejected_kept_when_sample_sufficient(self) -> None:
        """满样本的否决保留 — 终局判决交给毕业评审, 规则不自动翻案."""
        upd = _apply_hypothesis_rule(
            self._hyp("rejected"), _losing_trades(MIN_TRADES), self._vs(), has_own_state=True
        )
        assert upd is None

    def test_rejected_kept_without_own_strategy(self) -> None:
        """未进模拟盘的假设不被自动恢复 (has_own_state=False)."""
        upd = _apply_hypothesis_rule(
            self._hyp("rejected"), _losing_trades(3), self._vs(), has_own_state=False
        )
        assert upd is None

    def test_monitoring_outperform_becomes_validated(self) -> None:
        upd = _apply_hypothesis_rule(
            self._hyp("monitoring"),
            _winning_trades(MIN_TRADES),
            self._vs(outperforming=True, backtest_annual=12.0),
            has_own_state=True,
            own_nav=1.08,
        )
        assert upd is not None
        assert upd.to_status == "validated"
        assert "自身模拟盘" in upd.reason

    def test_promotion_requires_own_samples(self) -> None:
        """自身样本不足 → 不晋升 (不跟基策略的 vs.sample_sufficient 走)."""
        upd = _apply_hypothesis_rule(
            self._hyp("testing"),
            _winning_trades(MIN_TRADES - 1),
            self._vs(sample_sufficient=True, outperforming=True, backtest_annual=12.0),
            has_own_state=True,
            own_nav=1.5,
        )
        assert upd is None

    def test_promotion_requires_non_negative_own_nav(self) -> None:
        """自身亏着的策略不晋升 (哪怕基策略跑赢回测)."""
        upd = _apply_hypothesis_rule(
            self._hyp("testing"),
            _winning_trades(MIN_TRADES),
            self._vs(sample_sufficient=True, outperforming=True, backtest_annual=12.0),
            has_own_state=True,
            own_nav=0.99,
        )
        assert upd is None

    def test_promotion_requires_own_strategy(self) -> None:
        """没自己模拟盘策略的假设不晋升 (定时任务里 vs 是基策略的对照)."""
        upd = _apply_hypothesis_rule(
            self._hyp("testing"),
            _winning_trades(MIN_TRADES),
            self._vs(sample_sufficient=True, outperforming=True, backtest_annual=12.0),
            has_own_state=False,
            own_nav=None,
        )
        assert upd is None

    def test_monitoring_streak_stays_monitoring(self) -> None:
        """monitoring 再连亏不流转 (幂等, 不会累积成否决)."""
        upd = _apply_hypothesis_rule(self._hyp("monitoring"), _losing_trades(5), self._vs())
        assert upd is None


class TestStrategyLookupBySignalDefinition:
    """_strategy_for_sd — 策略 ↔ 假设关联键 = signal_definition (跨策略串扰防线)."""

    @staticmethod
    def _write_workbench(home: Path, records: list[dict]) -> None:
        wb = home / ".vibe-trading" / "workbench"
        wb.mkdir(parents=True, exist_ok=True)
        (wb / "strategies.json").write_text(
            json.dumps({"strategies": records}), encoding="utf-8"
        )

    def test_returns_matching_record_and_state(self, tmp_path: Path, monkeypatch) -> None:
        from src.strategy.review_engine import _strategy_for_sd, _strategy_state_for_sd

        run_dir = tmp_path / "runs" / "paper_s1"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(
            json.dumps({"nav": 1.02, "trades": [{"ret": 0.1}]}), encoding="utf-8"
        )
        self._write_workbench(
            tmp_path,
            [
                {"strategy_id": "combo_a", "signal_definition": "sd_a", "phase": "paper",
                 "run_dir": str(run_dir)},
                {"strategy_id": "combo_b", "signal_definition": "sd_b", "phase": "paused",
                 "run_dir": str(tmp_path / "nope")},
            ],
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        rec, state = _strategy_for_sd("sd_a")
        assert rec is not None and rec["strategy_id"] == "combo_a"
        assert state is not None and state["nav"] == 1.02
        only_state = _strategy_state_for_sd("sd_a")
        assert only_state is not None and only_state["nav"] == 1.02

    def test_unknown_signal_definition_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        from src.strategy.review_engine import _strategy_for_sd

        self._write_workbench(tmp_path, [{"strategy_id": "combo_a", "signal_definition": "sd_a"}])
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        assert _strategy_for_sd("sd_zzz") == (None, None)
        assert _strategy_for_sd("") == (None, None)

    def test_list_format_workbench_tolerated(self, tmp_path: Path, monkeypatch) -> None:
        """strategies.json 顶层是 list 时也要能查 (只认 dict 是历史脆弱点)."""
        from src.strategy.review_engine import _strategy_for_sd

        wb = tmp_path / ".vibe-trading" / "workbench"
        wb.mkdir(parents=True)
        (wb / "strategies.json").write_text(
            json.dumps([{"strategy_id": "combo_a", "signal_definition": "sd_a"}]), encoding="utf-8"
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        rec, _ = _strategy_for_sd("sd_a")
        assert rec is not None and rec["strategy_id"] == "combo_a"


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

    def test_consecutive_losses_targets_half(self) -> None:
        """连亏 3 笔 → 目标档位 0.5 (不是"每次调用再砍一半")."""
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        review = StrategyReview(
            vs_backtest=ReviewVsBacktest(consecutive_losses=3, sample_sufficient=True)
        )
        adaptations = compute_adaptations(review, {"exposure_multiplier": 1.0})

        assert len(adaptations) == 1
        assert adaptations[0].from_value == 1.0
        assert adaptations[0].to_value == 0.5

    def test_streak_three_does_not_double_cut(self) -> None:
        """已在档位 → 同一个连亏事件不重复砍 (旧实现连日连降, 30s 轮询几分钟砍到 0.25)."""
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        review = StrategyReview(vs_backtest=ReviewVsBacktest(consecutive_losses=4))
        assert compute_adaptations(review, {"exposure_multiplier": 0.5}) == []

    def test_streak_six_targets_floor(self) -> None:
        """深连亏 (≥6 笔) 才降到下限 0.25."""
        from src.strategy.review_engine import (
            EXPOSURE_MIN,
            ReviewVsBacktest,
            compute_adaptations,
        )

        review = StrategyReview(vs_backtest=ReviewVsBacktest(consecutive_losses=6))
        adaptations = compute_adaptations(review, {"exposure_multiplier": 1.0})

        assert adaptations[0].to_value == EXPOSURE_MIN

    def test_derisk_idempotent_under_polling(self) -> None:
        """回归 (2026-09-10): 同一连亏事件下反复调用 (GET 每 30s 轮询) 只降一次."""
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        review = StrategyReview(vs_backtest=ReviewVsBacktest(consecutive_losses=3))
        params = {"exposure_multiplier": 1.0}

        first = compute_adaptations(review, params)
        params["exposure_multiplier"] = first[0].to_value  # 调用方应用
        second = compute_adaptations(review, params)
        third = compute_adaptations(review, params)

        assert [a.to_value for a in first] == [0.5]
        assert second == []
        assert third == []

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

        assert adaptations == []  # 已在档位/下限, 不再降

    def test_risk_event_blocks_recovery(self) -> None:
        """风险事件期间不恢复 (旧实现只看"样本足+跑赢", 与风险事件可能同时成立)."""
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        review = StrategyReview(
            vs_backtest=ReviewVsBacktest(consecutive_losses=3, sample_sufficient=True,
                                         outperforming=True, current_dd=0.0)
        )
        assert compute_adaptations(review, {"exposure_multiplier": 0.25}) == []

    def test_recovery_once_per_day(self) -> None:
        """同日不重复恢复 (防 30s 轮询把仓位一路爬回 1.0)."""
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        today = datetime.now(timezone.utc).date().isoformat()
        review = StrategyReview(vs_backtest=ReviewVsBacktest())
        params = {"exposure_multiplier": 0.25, "exposure_recover_at": today}

        assert compute_adaptations(review, params) == []

    def test_recovery_dwell_blocks_right_after_derisk(self) -> None:
        """刚降过杠杆 → dwell 未满不允许恢复 (否则风控动作形同虚设)."""
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        review = StrategyReview(vs_backtest=ReviewVsBacktest())
        params = {"exposure_multiplier": 0.25}

        assert compute_adaptations(review, params, _derisk_history(days_ago=1)) == []

    def test_recovery_dwell_elapsed_allows_step_up(self) -> None:
        """dwell 已满 → 恢复一步 +0.1 并写下当日标记."""
        from src.strategy.review_engine import (
            EXPOSURE_RECOVER_DWELL_DAYS,
            ReviewVsBacktest,
            compute_adaptations,
        )

        review = StrategyReview(vs_backtest=ReviewVsBacktest())
        params = {"exposure_multiplier": 0.25}

        adaptations = compute_adaptations(
            review, params, _derisk_history(days_ago=EXPOSURE_RECOVER_DWELL_DAYS + 1)
        )

        assert [a.to_value for a in adaptations] == [0.35]
        assert params["exposure_recover_at"] == datetime.now(timezone.utc).date().isoformat()

    def test_outperforming_recovers_exposure(self) -> None:
        from src.strategy.review_engine import ReviewVsBacktest, compute_adaptations

        review = StrategyReview(
            vs_backtest=ReviewVsBacktest(
                sample_sufficient=True, outperforming=True, paper_trades=20
            )
        )
        adaptations = compute_adaptations(review, {"exposure_multiplier": 0.5})

        assert adaptations[0].from_value == 0.5
        assert adaptations[0].to_value == 0.6  # 证据快通道: 不受 dwell 限制

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


class TestReviewAdaptationsSerialization:
    """2026-09-06 回归: workbench GET 第三圈 extend dict 进 dataclass 列表导致 to_dict 崩.

    现象: _apply_adaptations 返回 dict 列表, 直接 r.adaptations.extend(...) 混入后
    StrategyReview.to_dict() 抛 'dict' object has no attribute 'to_dict' →
    review 静默变 {}, 整条策略复盘丢失 (日志累计 60 次间歇崩溃)。
    修复: ① routes 边界 ReviewAdaptation(**dict) 转 dataclass; ② to_dict 容错 dict 条目。
    """

    def test_to_dict_tolerates_dict_entries(self) -> None:
        from src.strategy.review_engine import ReviewAdaptation

        r = StrategyReview(adaptations=[ReviewAdaptation(param="exposure_multiplier", from_value=1.0, to_value=0.5, reason="dd breach")])
        # 模拟旧 bug: dict 混入 dataclass 列表
        r.adaptations.append({"param": "exposure_multiplier", "from_value": 1.0, "to_value": 0.5, "reason": "x", "at": "2026-09-06T00:00:00+00:00"})
        d = r.to_dict()  # 不应抛异常
        assert len(d["adaptations"]) == 2
        assert d["adaptations"][0]["param"] == "exposure_multiplier"
        assert d["adaptations"][1]["to_value"] == 0.5  # dict 条目原样透传

    def test_route_boundary_dict_to_dataclass_roundtrip(self) -> None:
        from src.strategy.review_engine import ReviewAdaptation

        raw = {"param": "exposure_multiplier", "from_value": 1.0, "to_value": 0.25, "reason": "consecutive losses"}
        a = ReviewAdaptation(**raw)  # routes 边界转换 (at 有默认值, 缺省合法)
        assert a.param == "exposure_multiplier"
        assert a.to_value == 0.25
        assert a.at  # 默认填充
        # dataclass 列表全为对象时 to_dict 序列化正常
        r = StrategyReview(adaptations=[a])
        d = r.to_dict()
        assert d["adaptations"][0] == {**raw, "at": a.at}
