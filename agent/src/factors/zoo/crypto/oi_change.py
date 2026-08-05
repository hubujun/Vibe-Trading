# ============================================================
# 中文名称: OI变动率
# 简要说明: 未平仓合约的日间变动率，(OI_t - OI_{t-1}) / OI_{t-1}。
# 典型用途: OI大幅增长 + 价格上涨 = 多头加仓信号；OI增长 + 价格下跌 = 空头加仓信号。
# ============================================================
"""crypto SENTIMENT: daily open interest change rate."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div

__alpha_meta__ = {
    "id": "crypto_oi_change",
    "nickname": "OI变动率",
    "theme": ["sentiment"],
    "formula_latex": "\\frac{\\mathrm{OI}_t - \\mathrm{OI}_{t-1}}{\\mathrm{OI}_{t-1}}",
    "columns_required": ["oi"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 2,
    "notes": "Open interest day-over-day change. Large positive = new money entering.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the day-over-day OI change percentage, aligned to close index.

    Relies on the panel loader to supply multi-row OI history (CCXT).  When
    only a single OI snapshot is available the loader intentionally omits OI
    from the panel entirely so the registry raises a clean SkipAlpha rather
    than producing an all-zero IC series (LAO-47, LAO-45).
    """
    oi = panel["oi"].astype(float)
    close = panel["close"]
    # Align to the close index via forward-fill (OI history may be sparser
    # than daily OHLCV).
    aligned = oi.reindex(index=close.index, method="ffill")
    oi_change = aligned.diff()
    return safe_div(oi_change, aligned.shift(1) + 1e-12)
