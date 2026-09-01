"""crypto_mined volume-volatility confluence: rolling correlation between range and volume."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_corr, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_volatility_confluence",
    "nickname": "VolumeVolatilityConfluence",
    "theme": ["volume"],
    "formula_latex": "F = \\mathrm{decay\\_linear}_5\\left( \\rho_{20}\\left(\\frac{H-L}{C}, V\\right) \\cdot \\mathrm{ts\\_rank}_{20}(V) \\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Combines rolling range-volume correlation with a rolling volume rank to capture periods where volatility expansion is accompanied by above-average volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-volatility confluence factor aligned to panel['close']."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_pct = safe_div(high - low, close)
    corr = ts_corr(range_pct, volume, 20)
    vol_rank = ts_rank(volume, 20)

    factor = decay_linear(corr * vol_rank, 5)
    return factor.reindex(index=close.index, columns=close.columns)