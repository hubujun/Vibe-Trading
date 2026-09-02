"""crypto volume: volume-per-range efficiency."""

import pandas as pd

from src.factors.base import rank, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_efficiency",
    "nickname": "量幅效率",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\mathrm{ts\\_rank}\\left(\\frac{V_t}{H_t-L_t}, 20\\right)\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "High values indicate that more volume was traded per unit of intraday range, a sign of unusually deep participation relative to price movement.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    high = panel["high"].reindex(index=close.index, columns=close.columns).astype(float)
    low = panel["low"].reindex(index=close.index, columns=close.columns).astype(float)
    volume = panel["volume"].reindex(index=close.index, columns=close.columns).astype(float)

    volume_per_range = safe_div(volume, high - low)
    return rank(ts_rank(volume_per_range, 20))