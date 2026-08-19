"""Tests for the variant auto-backtester (Loop Engineering 第四圈).

Covers: signal_definition parsing, factor module loading, backtest metrics
shape on a synthetic panel, promotion rules, and the full run pipeline
with an injected panel (no network).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.hypotheses.registry import HypothesisRegistry
from src.strategy.variant_backtester import (
    BASE_METRICS,
    backtest_variant,
    load_factor_module,
    parse_signal_definition,
    run_variant_backtests,
    _promote_status,
    _row_zscore,
)

__all__ = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _synthetic_panel(n_days: int = 300, n_symbols: int = 6) -> dict[str, pd.DataFrame]:
    """合成 panel: close 随机游走 + volume 随机, 足够过 BAB 的 252 预热期."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2025-01-01", periods=n_days, freq="D")
    close = pd.DataFrame(
        {f"S{i}": 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days))) for i in range(n_symbols)},
        index=idx,
    )
    volume = pd.DataFrame(
        {f"S{i}": rng.uniform(1e6, 5e6, n_days) for i in range(n_symbols)},
        index=idx,
    )
    return {"close": close, "volume": volume}


def _seed_variant(path: Path, sig_def: str, status: str = "exploring") -> HypothesisRegistry:
    registry = HypothesisRegistry(path)
    registry.create(
        title="测试变体",
        thesis="test",
        status=status,
        universe="crypto",
        signal_definition=sig_def,
        data_sources=["okx_candles_1d"],
        skills=["crypto"],
    )
    return registry


class TestParse:
    def test_with_factors(self) -> None:
        parsed = parse_signal_definition(
            "combo_variant: factors=[BAB,high52w,vol_x] weights={BAB:0.33,high52w:0.33,vol_x:0.33} top_n=2 bot_n=3"
        )
        assert parsed is not None
        assert parsed["factors"] == ["BAB", "high52w", "vol_x"]
        assert parsed["top_n"] == 2
        assert parsed["bot_n"] == 3

    def test_without_factors_derives_from_weights(self) -> None:
        parsed = parse_signal_definition(
            "combo_variant: weights={BAB:0.3,high52w:0.7} top_n=3 bot_n=3"
        )
        assert parsed is not None
        assert set(parsed["factors"]) == {"BAB", "high52w"}
        assert parsed["weights"]["BAB"] == 0.3

    def test_garbage_returns_none(self) -> None:
        assert parse_signal_definition("not a variant") is None
        assert parse_signal_definition("") is None
        assert parse_signal_definition("combo_variant: weights={}") is None


class TestLoadFactor:
    def test_academic_factor_loads(self) -> None:
        mod = load_factor_module("BAB")
        assert mod is not None and hasattr(mod, "compute")
        assert load_factor_module("high52w") is not None

    def test_zoo_factor_loads(self) -> None:
        mod = load_factor_module("volume_surge_reversal")
        assert mod is not None and hasattr(mod, "compute")

    def test_unknown_factor_returns_none(self) -> None:
        assert load_factor_module("no_such_factor_xyz") is None


class TestBacktest:
    def test_backtest_metrics_shape(self) -> None:
        panel = _synthetic_panel()
        metrics = backtest_variant(
            panel, ["BAB", "high52w"], {"BAB": 0.5, "high52w": 0.5}, 2, 2,
        )
        assert "error" not in metrics
        for key in ("annual", "sharpe", "max_dd", "cum", "turnover", "days", "factors"):
            assert key in metrics
        assert isinstance(metrics["annual"], float)
        assert metrics["days"] >= 260

    def test_row_zscore_standardizes(self) -> None:
        df = pd.DataFrame({"a": [1, 2, 5], "b": [3, 2, 1], "c": [5, 4, 3]})
        z = _row_zscore(df)
        # 每行均值为 0 (无零方差行)
        assert np.allclose(z.mean(axis=1), 0, atol=1e-9)
        # 零方差行 → NaN (设计行为)
        flat = pd.DataFrame({"a": [2, 2], "b": [2, 2]})
        assert _row_zscore(flat).isna().all().all()


class TestPromotion:
    def test_promote_when_beats_base(self) -> None:
        metrics = {
            "annual": BASE_METRICS["annual"] + 5,
            "sharpe": BASE_METRICS["sharpe"] + 0.3,
            "max_dd": BASE_METRICS["max_dd"] * 1.2,  # 回撤可控
        }
        assert _promote_status(metrics) == "testing"

    def test_stay_exploring_when_loses(self) -> None:
        metrics = {
            "annual": BASE_METRICS["annual"] - 10,
            "sharpe": BASE_METRICS["sharpe"] - 0.5,
            "max_dd": BASE_METRICS["max_dd"] * 2,  # 回撤超限
        }
        assert _promote_status(metrics) == "exploring"

    def test_incomplete_metrics_stay_exploring(self) -> None:
        assert _promote_status({"annual": 20.0}) == "exploring"


class TestRunPipeline:
    def test_full_run_with_synthetic_panel(self, tmp_path: Path) -> None:
        hypo_path = tmp_path / "h.json"
        cache_path = tmp_path / "cache.json"
        _seed_variant(
            hypo_path,
            "combo_variant: factors=[BAB,high52w] weights={BAB:0.5,high52w:0.5} top_n=2 bot_n=2",
        )
        panel = _synthetic_panel()

        result = run_variant_backtests(
            max_per_run=5,
            hypotheses_path=hypo_path,
            cache_path=cache_path,
            panel=panel,
        )

        assert len(result["backtested"]) == 1
        assert result["backtested"][0]["metrics"]["annual"] is not None
        # 缓存已写入 (含动态基准 _BASE_)
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        assert "_BASE_" in cache
        assert len([k for k in cache if k.startswith("combo_variant")]) == 1

    def test_cached_variant_skipped(self, tmp_path: Path) -> None:
        hypo_path = tmp_path / "h.json"
        cache_path = tmp_path / "cache.json"
        sig = "combo_variant: factors=[BAB,high52w] weights={BAB:0.5,high52w:0.5} top_n=2 bot_n=2"
        _seed_variant(hypo_path, sig)
        cache_path.write_text(
            json.dumps({sig: {"annual": 15.0, "sharpe": 1.0, "max_dd": -5.0}}), encoding="utf-8"
        )
        panel = _synthetic_panel()

        result = run_variant_backtests(
            max_per_run=5, hypotheses_path=hypo_path, cache_path=cache_path, panel=panel,
        )

        assert result["backtested"] == []
        assert result["skipped"] == 0

    def test_no_candidates(self, tmp_path: Path) -> None:
        hypo_path = tmp_path / "h.json"
        cache_path = tmp_path / "cache.json"
        _seed_variant(hypo_path, "combo_variant: weights={BAB:0.5,high52w:0.5}", status="testing")
        panel = _synthetic_panel()

        result = run_variant_backtests(
            max_per_run=5, hypotheses_path=hypo_path, cache_path=cache_path, panel=panel,
        )

        assert result["backtested"] == []

    def test_auto_seed_idempotent(self, tmp_path: Path, monkeypatch) -> None:
        """晋升变体自动播种; 同 signal_definition 重复播种被去重跳过."""
        from src.strategy import variant_backtester as vb

        strategies_path = tmp_path / "strategies.json"
        monkeypatch.setattr(vb, "_STRATEGIES_PATH", strategies_path)

        sd = "combo_variant: factors=[BAB,high52w,testf] weights={BAB:0.33,high52w:0.33,testf:0.33} top_n=3 bot_n=3"
        sid1 = vb._auto_seed_strategy(sd, "加入测试因子 testf")
        assert sid1 and sid1.startswith("combo_")

        data = json.loads(strategies_path.read_text(encoding="utf-8"))
        assert len(data["strategies"]) == 1
        assert data["strategies"][0]["phase"] == "paper"
        assert data["strategies"][0]["signal_definition"] == sd
        assert data["strategies"][0]["universe_size"] == len(vb.SYMBOLS)

        # 幂等: 重复播种返回 None, 不新增
        assert vb._auto_seed_strategy(sd, "again") is None
        assert len(json.loads(strategies_path.read_text(encoding="utf-8"))["strategies"]) == 1
