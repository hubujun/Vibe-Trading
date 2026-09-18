"""crypto VOLUME: volume-weighted reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_reversal",
    "nickname": "量能加权反转",
    "theme": ["volume"],
    "formula_latex": r"- zscore\left(\frac{close_t}{close_{t-1}} - 1\right) \cdot \mathrm{ts\_rank}\left(\frac{volume_t}{\mathrm{ts\_mean}(volume, 20)} - 1, 10\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 21,
    "notes": "Reversal signal weighted by abnormal volume rank; high volume shocks against recent returns.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-weighted reversal factor aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)
    ret = safe_div(close, close.shift(1)) - 1.0
    vol_base = ts_mean(volume, 20)
    vol_shock = safe_div(volume, vol_base) - 1.0
    return -zscore(ret) * ts_rank(vol_shock, 10)