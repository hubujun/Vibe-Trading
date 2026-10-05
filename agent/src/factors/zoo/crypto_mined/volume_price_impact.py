"""crypto VOLUME: decayed price impact per unit of typical traded volume."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, safe_div, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_impact",
    "nickname": "量价冲击衰减",
    "theme": ["volume"],
    "formula_latex": (
        "z\\left(\\mathrm{decay}_{10}\\left("
        "\\frac{\\Delta C_t}{\\mathrm{mean}_{20}(V_t)}\\right)\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 30,
    "notes": (
        "Per-bar close change divided by the 20-bar average volume, i.e. the price "
        "impact achieved by one unit of 'typical' turnover. Smoothed with a 10-bar "
        "linear decay and cross-sectionally z-scored. High values mean the market "
        "moves aggressively on relatively little volume, a signature of thin books "
        "and latent demand pressure."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the decayed, cross-sectionally standardised volume price impact."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    typical_volume = ts_mean(volume, 20)
    impact = safe_div(delta(close, 1), typical_volume)
    smoothed = decay_linear(impact, 10)
    return zscore(smoothed)