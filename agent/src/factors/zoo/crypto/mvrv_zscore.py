# ============================================================
# 中文名称: MVRV Z-Score
# 简要说明: MVRV偏离其历史均值的标准差倍数。 > 1 = 高估, < -1 = 低估。
# 典型用途: 极端值（>3 或 <-2）作为长期顶部/底部信号。
# ============================================================
"""crypto VALUE: MVRV Z-Score — deviation from historical mean."""

from __future__ import annotations

import pandas as pd

from src.factors.base import ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mvrv_zscore",
    "nickname": "MVRV Z分数",
    "theme": ["value"],
    "formula_latex": (
        r"\frac{\mathrm{MVRV}_t - \mathrm{MA}(\mathrm{MVRV}, 365)}"
        r"{\sigma(\mathrm{MVRV}, 365)}"
    ),
    "columns_required": ["onchain:mvrv"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 180,
    "min_warmup_bars": 365,
    "notes": (
        "MVRV Z-Score = (raw MVRV - 365-day MA) / 365-day std. "
        "Values > 3 historically signal cycle tops, < -2 signal bottoms."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return MVRV Z-Score: z-score of raw MVRV over a 365-day rolling window."""
    mvrv = panel["onchain:mvrv"].astype(float)
    ma = ts_mean(mvrv, 365)
    std = ts_std(mvrv, 365)
    return (mvrv - ma) / (std + 1e-12)
