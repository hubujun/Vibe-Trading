"""crypto_mined_volume_reversal_liquidity: volume-scaled intraday reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_reversal_liquidity",
    "nickname": "量幅反转",
    "theme": ["reversal", "liquidity"],
    "formula_latex": r"-\mathrm{zscore}_{cs}\left(\mathrm{rank}_{cs}(\mathrm{ts\_rank}(V,20)) \cdot \mathrm{rank}_{cs}((H-L)/L) \cdot (C/O-1)\right)",
    "columns_required": ["open", "high", "low", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "High-volume, wide-range upward moves are shorted; downward moves are longed.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-scaled intraday reversal signal aligned to the close panel."""
    close = panel["close"].astype(float)
    open_ = panel["open"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    body = safe_div(close, open_) - 1.0
    range_ = safe_div(high, low) - 1.0
    vol_ts = ts_rank(volume, 20)

    signal = rank(vol_ts) * rank(range_) * body
    return -zscore(signal)