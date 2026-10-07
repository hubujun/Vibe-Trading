"""crypto VOLUME: rolling volume-range coupling regime."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_corr, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_range_coupling",
    "nickname": "量幅耦合度",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{ts\\_rank}\\left(\\mathrm{corr}\\left(V_t,\\; \\frac{H_t-L_t}{C_t},\\,20\\right),\\,60\\right)",
    "columns_required": ["volume", "high", "low", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 80,
    "notes": "Rolling 20-bar correlation between volume and the intraday range fraction (H-L)/C. High values flag volume-driven range-expansion regimes; the correlation is then ranked over 60 bars per instrument.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the 60-bar rolling rank of volume vs range-fraction correlation."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    range_frac = safe_div(high - low, close)
    coupling = ts_corr(volume, range_frac, 20)
    return ts_rank(coupling, 60)