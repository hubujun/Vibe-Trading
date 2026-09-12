"""crypto reversal/volume: volume-weighted five-day reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_reversal",
    "nickname": "量价加权反转",
    "theme": ["reversal", "volume"],
    "formula_latex": r"\mathrm{rank}_{cs}\!\left(-\mathrm{rank}_{cs}\!\left(\frac{\Delta_5 close}{close_{t-5}}\right)\cdot \mathrm{rank}_{cs}\!\left(\frac{volume}{\mathrm{tsmean}_{20}(volume)}\right)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "Five-day price reversal amplified when recent volume is high; cross-sectional rank composite.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret5 = safe_div(delta(close, 5), close.shift(5))
    vol_ratio = safe_div(volume, ts_mean(volume, 20))
    raw = -rank(ret5) * rank(vol_ratio)
    return rank(raw) - 0.5