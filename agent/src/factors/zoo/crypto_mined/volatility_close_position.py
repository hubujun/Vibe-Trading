"""crypto volatility: low-volatility persistent close location."""

from __future__ import annotations

import pandas as pd

from src.factors.base import (
    delta,
    rank,
    safe_div,
    ts_rank,
    ts_std,
    zscore,
)

__alpha_meta__ = {
    "id": "crypto_mined_volatility_close_position",
    "nickname": "VolatilityClosePosition",
    "theme": ["volatility"],
    "formula_latex": r"F_t = -\mathrm{zscore}(\sigma_{20}(r_t)) \cdot \mathrm{rank}\left(\mathrm{ts\_rank}\left(\frac{C_t-L_t}{H_t-L_t},20\right)\right)",
    "columns_required": ["close", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 20,
    "notes": "Rewards low volatility combined with close prices persistently sitting near the intraday range high. NaN is preserved.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    volatility = ts_std(ret, 20)

    close_loc = safe_div(close - low, high - low)
    loc_rank = ts_rank(close_loc, 20)

    factor = -zscore(volatility) * rank(loc_rank)

    return factor.reindex(index=close.index, columns=close.columns)