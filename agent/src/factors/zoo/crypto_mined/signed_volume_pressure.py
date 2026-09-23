"""crypto VOLUME: signed volume pressure."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_signed_volume_pressure",
    "nickname": "有向量压",
    "theme": ["volume"],
    "formula_latex": "\\operatorname{rank}(\\operatorname{ts\\_mean}(V_t \\cdot \\frac{2C_t - H_t - L_t}{H_t - L_t}, 10))",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 11,
    "notes": "Volume multiplied by close location value, averaged over 10 bars and cross-sectionally ranked. Positive readings indicate volume concentrated near the high.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return cross-sectional rank of signed volume pressure."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    spread = high - low
    close_location = safe_div(2.0 * close - high - low, spread)
    signed_volume = volume * close_location
    pressure = ts_mean(signed_volume, 10)
    return rank(pressure)