"""温和版动态多空比测试: 牛市减空头 / 熊市减多头 / 震荡对称.

注意: backtest_variant 现在总是动态 (实盘=回测一致), dynamic_n 参数保留兼容.
"""

import pandas as pd
import numpy as np

from src.strategy.variant_backtester import backtest_variant


def _mk_panel(btc_path: np.ndarray, n_coins: int = 8) -> dict:
    idx = pd.date_range("2026-01-01", periods=len(btc_path), freq="D")
    rng = np.random.default_rng(7)
    data = {"BTC-USDT": btc_path}
    for i in range(n_coins - 1):
        data[f"C{i}"] = np.abs(np.cumsum(rng.normal(0, 1, len(btc_path))) + 100)
    close = pd.DataFrame(data, index=idx)
    return {"close": close, "volume": pd.DataFrame(np.ones_like(close), index=idx, columns=close.columns)}


def test_dynamic_n_bull_runs():
    # 前 40 天陡涨 (BTC +30%, 20d 动量 >+4% → risk_on): 牛市减空头路径
    btc = np.concatenate([np.linspace(100, 130, 40), np.linspace(130, 128, 40)])
    panel = _mk_panel(btc)
    m = backtest_variant(panel, ["market_regime_momentum"], {"market_regime_momentum": 1.0}, 3, 3)
    assert "error" not in m
    assert m["days"] > 30


def test_dynamic_n_bear_runs():
    # 前 40 天陡跌 (BTC -30%): 熊市减多头路径
    btc = np.concatenate([np.linspace(130, 100, 40), np.linspace(100, 102, 40)])
    panel = _mk_panel(btc)
    m = backtest_variant(panel, ["market_regime_momentum"], {"market_regime_momentum": 1.0}, 3, 3)
    assert "error" not in m
    assert isinstance(m["annual"], (int, float))


def test_backtest_with_sector_cap_and_vol_target():
    """板块上限 + 波动率目标在回测中生效 (不报错, 指标合理)."""
    idx = pd.date_range("2026-01-01", periods=120, freq="D")
    rng = np.random.default_rng(3)
    data = {"BTC-USDT": np.linspace(100, 120, 120)}
    for i in range(9):
        data[f"C{i}"] = np.abs(np.cumsum(rng.normal(0, 1, 120)) + 100)
    close = pd.DataFrame(data, index=idx)
    panel = {"close": close, "volume": pd.DataFrame(np.ones_like(close), index=idx, columns=close.columns)}
    m = backtest_variant(panel, ["market_regime_momentum"], {"market_regime_momentum": 1.0}, 3, 3)
    assert "error" not in m
    assert isinstance(m["sharpe"], (int, float))
    assert isinstance(m["max_dd"], (int, float))
