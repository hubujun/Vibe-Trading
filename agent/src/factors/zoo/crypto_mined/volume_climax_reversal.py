"""crypto VOLUME: volume climax reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_climax_reversal",
    "nickname": "Volume Climax Reversal",
    "theme": ["volume"],
    "formula_latex": "-\\left(\\mathrm{rank}\\!\\left(\\frac{V_t}{\\mathrm{tsmean}(V,20)}\\right)-0.5\\right)\\left(\\frac{C_t}{\\mathrm{tsmean}(C,20)}-1\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "High relative volume combined with price stretched above its recent mean is treated as a climax and faded.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-climax reversal score aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    vol_ratio = safe_div(volume, ts_mean(volume, 20))
    price_stretch = safe_div(close, ts_mean(close, 20)) - 1.0
    return -(rank(vol_ratio) - 0.5) * price_stretch