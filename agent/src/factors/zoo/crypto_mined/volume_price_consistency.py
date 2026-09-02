"""Volume-price consistency: rolling return-volume correlation plus rolling volume rank."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_corr, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_consistency",
    "nickname": "VolumePriceConsistency",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{rank}\left( \rho_{8}\left(\frac{C-O}{O}, \frac{\Delta V}{V_{t-1}}\right)\right) + \mathrm{rank}\left( \mathrm{ts\_rank}_{20}(V)\right)",
    "columns_required": ["close", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 8,
    "min_warmup_bars": 20,
    "notes": "Combines rolling correlation between intraday return and volume change with a rolling volume-level rank. NaN is propagated.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    open_ = panel["open"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(close - open_, open_)
    volume_change = safe_div(delta(volume, 1), volume.shift(1))

    return_corr = ts_corr(ret, volume_change, 8)
    volume_level_rank = ts_rank(volume, 20)

    return rank(return_corr) + rank(volume_level_rank)