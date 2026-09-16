"""crypto VOLUME: deviation from rolling VWAP amplified by volume expansion."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, signed_power, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_vwap_volume_reversion",
    "nickname": "VWAP Volume Reversion",
    "theme": ["volume"],
    "formula_latex": (
        "\\frac{\\mathrm{VWMA}_t - C_t}{\\mathrm{VWMA}_t} \\cdot "
        "\\left(\\frac{\\overline{V}_{5}}{\\overline{V}_{20}}\\right)^{1/2},"
        "\\quad \\mathrm{VWMA}_t = \\frac{\\sum_{i=0}^{n-1} C_{t-i} V_{t-i}}"
        "{\\sum_{i=0}^{n-1} V_{t-i}}"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 8,
    "min_warmup_bars": 20,
    "notes": (
        "Signed distance of close from its 20-bar volume-weighted moving average, "
        "scaled by the square root of short/long volume expansion. Positive values "
        "flag price trading below its volume-weighted fair level while turnover is "
        "accelerating (capitulation-style reversion candidate); negative values flag "
        "extended price on expanding volume."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-expansion-scaled VWAP deviation, aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    n = 20
    vwma = safe_div(ts_mean(close * volume, n), ts_mean(volume, n))

    deviation = safe_div(vwma - close, vwma)
    vol_expansion = safe_div(ts_mean(volume, 5), ts_mean(volume, n))

    return deviation * signed_power(vol_expansion, 0.5)