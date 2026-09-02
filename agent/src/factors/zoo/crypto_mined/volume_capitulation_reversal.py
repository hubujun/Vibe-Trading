"""Crypto mined factor: high-volume drawdowns as a reversal signal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_max, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_capitulation_reversal",
    "nickname": "VolCapitulationReversal",
    "theme": ["volume"],
    "formula_latex": r"\operatorname{rank}\left(\left(\operatorname{ts\_rank}(V,20)-0.5\right)\cdot\frac{\mathrm{MAX}_{20}(P)-P_t}{\mathrm{MAX}_{20}(P)}\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 21,
    "notes": "Combines a high trailing volume percentile with a drawdown relative to the 20-bar high.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    vol_rank = ts_rank(volume, 20) - 0.5
    high_20 = ts_max(close, 20)
    drawdown = safe_div(high_20 - close, high_20)
    signal = vol_rank * drawdown

    return rank(signal).reindex(index=close.index, columns=close.columns)