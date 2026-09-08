"""Crypto VOLUME: range position conditioned on volume rank."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_max, ts_min, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_position",
    "nickname": "Volume Range Position",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{rank}\left( \mathrm{ts\_rank}_{20}(v_t) \cdot \left(\frac{c_t-\mathrm{ts\_min}_{20}(l_t)}{\mathrm{ts\_max}_{20}(h_t)-\mathrm{ts\_min}_{20}(l_t)} - 0.5 \right) \right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "Measures whether high volume occurs near the upper or lower boundary of the recent high-low range. No missing values are filled.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the cross-sectional rank of volume-conditioned range position."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float).reindex(index=close.index, columns=close.columns)
    low = panel["low"].astype(float).reindex(index=close.index, columns=close.columns)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    window = 20
    upper = ts_max(high, window)
    lower = ts_min(low, window)

    position = safe_div(close - lower, upper - lower)
    volume_state = ts_rank(volume, window)
    raw = volume_state * (position - 0.5)

    return rank(raw).reindex(index=close.index, columns=close.columns)