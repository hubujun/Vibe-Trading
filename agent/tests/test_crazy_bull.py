"""疯牛保险测试 — 普涨环境降仓, 分化/下跌环境不触发.

验证 _crazy_bull_mult 的行为本身 (信号构造), 与 backtest_variant/daily_signal 共用.
"""
import numpy as np
import pandas as pd
import pytest

from src.strategy.variant_backtester import (
    _crazy_bull_mult,
    CRAZY_BULL_MULT,
    CRAZY_BREADTH_TH,
    CRAZY_MOM_TH,
    CRAZY_BTC_TH,
)


def _mk_close(days: int = 80, n_coins: int = 8, seed: int = 7) -> pd.DataFrame:
    """随机游走面板 (无普涨) + BTC 列."""
    idx = pd.date_range("2024-01-01", periods=days, freq="D")
    rng = np.random.default_rng(seed)
    close = pd.DataFrame(
        100 + np.cumsum(rng.standard_normal((days, n_coins)), axis=0),
        index=idx,
        columns=[f"C{i}-USDT" for i in range(n_coins)],
    )
    close["BTC-USDT"] = close["C0-USDT"]
    return close


def test_normal_market_no_insurance():
    """随机游走 (无普涨) → 全部 1.0, 不触发."""
    close = _mk_close()
    mult = _crazy_bull_mult(close)
    assert (mult == 1.0).all()


def test_crazy_bull_triggers_on_universal_rally():
    """所有币普涨 20% (广度 100%, BTC 动量 >8%) → 触发 0.4."""
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    close = pd.DataFrame(100.0, index=idx, columns=[f"C{i}-USDT" for i in range(8)])
    # 前 55 天横盘, 后 20 天每天 +1% 普涨 (20日动量 ≈ +20%)
    for i in range(8):
        close.loc[idx[55:], f"C{i}-USDT"] = 100 * (1.01 ** np.arange(1, 26))
    close["BTC-USDT"] = close["C0-USDT"]
    mult = _crazy_bull_mult(close)
    # 横盘期不触发
    assert mult.loc[idx[30]] == 1.0
    # 普涨末端触发 (20日动量 >15% 的币占 9/9, BTC 动量 >8%)
    assert mult.iloc[-1] == pytest.approx(CRAZY_BULL_MULT)


def test_btc_up_but_narrow_breadth_no_trigger():
    """BTC 大涨但广度不足 (仅 2/9 币动量 >15%) → 不触发."""
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    close = pd.DataFrame(100.0, index=idx, columns=[f"C{i}-USDT" for i in range(8)])
    # 只有 C0/BTC 涨, 其余横盘
    close.loc[idx[55:], "C0-USDT"] = 100 * (1.01 ** np.arange(1, 26))
    close["BTC-USDT"] = close["C0-USDT"]
    mult = _crazy_bull_mult(close)
    assert mult.iloc[-1] == 1.0  # 广度 2/9 ≈ 22% < 50% → 不触发


def test_broad_rally_but_btc_flat_no_trigger():
    """alt 普涨但 BTC 动量不足 (≤8%) → 不触发 (BTC 领涨是普涨轧空前提)."""
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    close = pd.DataFrame(100.0, index=idx, columns=[f"C{i}-USDT" for i in range(8)])
    # 7 个 alt 普涨, BTC 横盘
    for i in range(1, 8):
        close.loc[idx[55:], f"C{i}-USDT"] = 100 * (1.01 ** np.arange(1, 26))
    close["BTC-USDT"] = 100.0
    mult = _crazy_bull_mult(close)
    # 广度 7/9 ≈ 78% > 50%, 但 BTC 20d 动量 ≈ 0 → 不触发
    assert mult.iloc[-1] == 1.0


def test_constants_consistent():
    """常量自洽: 阈值在 (0,1) 且乘数在 (0,1)."""
    assert 0 < CRAZY_BULL_MULT < 1
    assert 0 < CRAZY_BREADTH_TH < 1
    assert CRAZY_MOM_TH > 0
    assert CRAZY_BTC_TH > 0
