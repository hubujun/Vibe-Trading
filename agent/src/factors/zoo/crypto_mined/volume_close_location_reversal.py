"""Volume-scaled close location reversal: buys volume-backed weakness, sells volume-backed strength."""

import pandas as pd

from src.factors.base import rank, safe_div

__alpha_meta__ = {
    "id": "crypto_mined_volume_close_location_reversal",
    "nickname": "Volume Close-Location Reversal",
    "theme": ["volume"],
    "formula_latex": "F_t = -\\left(\\frac{C_t-L_t}{H_t-L_t} - 0.5\\right) \\cdot \\mathrm{rank}_t(V_t)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 0,
    "notes": "Reversal signal using intraday close position and cross-sectional volume rank. Strong volume near the high flips the signal short; strong volume near the low flips it long.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    close_location = safe_div(close - low, high - low)
    volume_rank = rank(volume)

    factor = -(close_location - 0.5) * volume_rank
    return factor