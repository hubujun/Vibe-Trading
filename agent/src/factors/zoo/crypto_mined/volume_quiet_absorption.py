"""Quiet-market volume absorption in crypto."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean, ts_std, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_quiet_absorption",
    "nickname": "Quiet Volume Absorption",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{F}_t = \mathrm{zscore}\left[ \mathrm{rank}\left(-\sigma_{20}(\Delta C_t)\right) \cdot \mathrm{rank}\left(\frac{V_t}{\mathrm{SMA}_{20}(V_t)}\right) \right]",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 21,
    "notes": "High volume during abnormally quiet price action may signal accumulation/absorption before a move.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume absorption signal, aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    price_vol = ts_std(delta(close, 1), 20)
    volume_surge = safe_div(volume, ts_mean(volume, 20))

    absorption = rank(-price_vol) * rank(volume_surge)
    return zscore(absorption)