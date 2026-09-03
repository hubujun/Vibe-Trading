"""Crypto volume factor: late volume climax confirmed by a 10-bar range position."""

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_argmax, ts_max, ts_min, zscore

__alpha_meta__ = {
    "id": "crypto_mined_late_volume_climax_flow",
    "nickname": "尾段量能确认",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{zscore}\left( \operatorname{DWL}_3\left( \frac{C_t-\min_{10}(L)}{\max_{10}(H)-\min_{10}(L)} \cdot \operatorname{argmax}_{10}(V) \right) \right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 12,
    "notes": "High when price closes near the upper 10-bar boundary and the highest volume bar occurred late in the 10-bar lookback window.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return a cross-sectional late-volume price-confirmation signal."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float).reindex_like(close)
    low = panel["low"].astype(float).reindex_like(close)
    volume = panel["volume"].astype(float).reindex_like(close)

    pos = safe_div(
        close - ts_min(low, 10),
        ts_max(high, 10) - ts_min(low, 10),
    )
    volume_clock = ts_argmax(volume, 10)

    return zscore(decay_linear(pos * volume_clock, 3))