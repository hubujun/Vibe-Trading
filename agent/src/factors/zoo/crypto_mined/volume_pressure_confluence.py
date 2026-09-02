"""Crypto mined factor: volume-confirmed short-horizon price trend."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, signed_power, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_pressure_confluence",
    "nickname": "VolConfirmedMomentum",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{rank}\left(\operatorname{ts\_rank}(V,20)^{0.5} \cdot \operatorname{ts\_rank}\left(\mathrm{MA}_5\left(\frac{\Delta P}{P}\right),20\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 30,
    "notes": "Cross-sectional rank of volume percentile interacted with smoothed short-term return rank.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    price_ret = safe_div(delta(close, 1), close.shift(1))
    ret_smooth = ts_mean(price_ret, 5)
    volume_pressure = ts_rank(volume, 20)
    flow = signed_power(volume_pressure, 0.5) * ts_rank(ret_smooth, 20)

    return rank(flow).reindex(index=close.index, columns=close.columns)