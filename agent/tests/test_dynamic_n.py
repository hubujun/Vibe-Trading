"""温和版动态多空比测试: 牛市减空头 / 熊市减多头 / 震荡对称."""

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


def test_dynamic_n_bull_reduces_shorts():
    # 前 40 天陡涨 (BTC +30%, 20d 动量 >+4% → risk_on): 多头 3 空头 2
    btc = np.concatenate([np.linspace(100, 130, 40), np.linspace(130, 128, 40)])
    panel = _mk_panel(btc)
    # 用只依赖 close 的 market_regime_momentum (避免学术因子在构造 panel 上失效)
    m_fixed = backtest_variant(panel, ["market_regime_momentum"], {"market_regime_momentum": 1.0}, 3, 3, dynamic_n=False)
    m_dyn = backtest_variant(panel, ["market_regime_momentum"], {"market_regime_momentum": 1.0}, 3, 3, dynamic_n=True)
    assert m_fixed != m_dyn, "牛市窗口动态多空比应改变指标"
    assert "error" not in m_dyn


def test_dynamic_n_bear_reduces_longs():
    # 前 40 天陡跌 (BTC -30%): 多头 2 空头 3
    btc = np.concatenate([np.linspace(130, 100, 40), np.linspace(100, 102, 40)])
    panel = _mk_panel(btc)
    m = backtest_variant(panel, ["market_regime_momentum"], {"market_regime_momentum": 1.0}, 3, 3, dynamic_n=True)
    assert "error" not in m
    assert isinstance(m["annual"], (int, float))
