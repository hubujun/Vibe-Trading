# ============================================================
# 中文名称: 日内振幅
# 简要说明: (high - low) / open，衡量日内价格波动幅度。
# 典型用途: 振幅放大 + 成交量放大 = 趋势启动信号；震荡市中振幅收窄。
# ============================================================
"""crypto VOLATILITY: intraday amplitude = (high - low) / open."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div

__alpha_meta__ = {
    "id": "crypto_amplitude",
    "nickname": "日内振幅",
    "theme": ["volatility"],
    "formula_latex": (
        "\\frac{\\mathrm{high} - \\mathrm{low}}{\\mathrm{open}}"
    ),
    "columns_required": ["open", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d", "4h"],
    "decay_horizon": 5,
    "min_warmup_bars": 1,
    "notes": "Intraday price range as fraction of open. Wide range = high volatility day.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return (high - low) / open = intraday amplitude."""
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    open_ = panel["open"].astype(float)
    return safe_div(high - low, open_ + 1e-12)
