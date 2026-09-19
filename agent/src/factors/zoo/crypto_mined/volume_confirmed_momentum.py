"""crypto MOMENTUM: volume-confirmed trend via return/volume concordance."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_confirmed_momentum",
    "nickname": "量能确认动量",
    "theme": ["momentum", "volume"],
    "formula_latex": (
        "\\rho_{20}\\!\\left(|r_t|,\\; \\frac{V_t}{\\mathrm{MA}_{20}(V)_t}\\right)"
        "\\cdot \\left(\\frac{C_t}{\\mathrm{MA}_{20}(C)_t} - 1\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 4,
    "min_warmup_bars": 22,
    "notes": (
        "Rolling 20-bar correlation between absolute return and relative volume is "
        "used as a regime gate: when big moves are reliably accompanied by big "
        "volume, the market is in a volume-confirmed trend regime and the 20-bar "
        "trend estimate is trusted. In choppy, volume-decoupled regimes the gate "
        "shrinks the signal toward zero while preserving its sign."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-confirmed trend score aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    vol_ratio = safe_div(volume, ts_mean(volume, 20))

    concordance = ts_corr(ret.abs(), vol_ratio, 20)

    trend = safe_div(close, ts_mean(close, 20)) - 1.0
    return concordance * trend