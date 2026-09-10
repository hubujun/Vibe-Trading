"""Volume-shock mean reversion: extreme five-bar close moves on above-average own volume."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_rank, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_vol_shock_reversion",
    "nickname": "VolShockReversion",
    "theme": ["volume", "reversal"],
    "formula_latex": r"rank_t\left(-\frac{\Delta_5 C_t}{\sigma_{20}(C_t)} \cdot \mathrm{ts\_rank}_{20}(V_t)\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 20,
    "notes": "Contrarian signal that only fires when a 5-bar price shock is accompanied by elevated rolling volume percentile.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    price_shock = safe_div(delta(close, 5), ts_std(close, 20))
    vol_intensity = ts_rank(volume, 20)

    alpha = -price_shock * vol_intensity
    return rank(alpha)