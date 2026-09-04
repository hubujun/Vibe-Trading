"""Crypto mined factor: volume-spike reversals."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_rank, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_spike_reversal",
    "nickname": "VolumeSpikeReversal",
    "theme": ["volume", "reversal"],
    "formula_latex": "\\operatorname{rank}_{cs}\\left(-\\operatorname{ts\\_rank}_{20}(V_t) \\cdot \\frac{\\Delta C_t / C_{t-1}}{\\sigma_{20}(\\Delta C/C)}\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 21,
    "notes": "Fade one-bar price moves that happen on relatively high 20-bar volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    ret_vol = ts_std(ret, 20)
    price_flow = safe_div(ret, ret_vol + 1e-12)
    volume_rank = ts_rank(volume, 20)

    return rank(-volume_rank * price_flow)