"""Reversal pressure from upper/lower wick imbalance."""

import pandas as pd

from src.factors.base import safe_div, ts_mean, decay_linear

__alpha_meta__ = {
    "id": "crypto_mined_wick_reversal_pressure",
    "nickname": "WickReversal",
    "theme": ["reversal"],
    "formula_latex": "-\\frac{(H_t-\\max(O_t,C_t))-(\\min(O_t,C_t)-L_t)}{H_t-L_t} \\cdot \\frac{V_t}{\\mathrm{mean}_{20}(V_t)}",
    "columns_required": ["close", "high", "low", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 4,
    "min_warmup_bars": 23,
    "notes": "Negative wick imbalance (lower wick dominance) with high volume is a bullish reversal pressure.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    open_ = panel["open"].astype(float)
    volume = panel["volume"].astype(float)

    candle_range = high - low
    wick_balance = safe_div(high + low - (open_ + close), candle_range)
    volume_ratio = safe_div(volume, ts_mean(volume, 20))

    raw = -wick_balance * volume_ratio
    return decay_linear(raw, 4)