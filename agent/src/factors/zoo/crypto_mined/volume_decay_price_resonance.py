"""crypto_mined_volume_decay_price_resonance: decayed volume vs price return resonance."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_decay_price_resonance",
    "nickname": "Volume Decay Price Resonance",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\mathrm{ts\\_corr}_{20}\\left(\\mathrm{decay\\_linear}_{10}(V), \\mathrm{decay\\_linear}_{10}(r)\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 40,
    "notes": "Correlation between decayed volume and decayed return; captures whether volume flow reinforces short-term price drift.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    ret = safe_div(delta(close, 1), close.shift(1))
    vol_flow = decay_linear(volume, 10)
    ret_flow = decay_linear(ret, 10)
    resonance = ts_corr(vol_flow, ret_flow, 20)

    return rank(resonance)