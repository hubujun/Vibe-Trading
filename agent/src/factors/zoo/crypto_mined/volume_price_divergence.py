"""crypto VOLUME: volume-price divergence via rank spread."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_divergence",
    "nickname": "量价背离",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\frac{\\Delta V_5}{V_{t-5}}\\right) - \\mathrm{rank}\\left(\\frac{\\Delta C_5}{C_{t-5}}\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 7,
    "notes": "Cross-sectional rank spread between 5-day volume growth and 5-day price return.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    price_mom = safe_div(delta(close, 5), close.shift(5))
    volume_mom = safe_div(delta(volume, 5), volume.shift(5))
    return rank(volume_mom) - rank(price_mom)