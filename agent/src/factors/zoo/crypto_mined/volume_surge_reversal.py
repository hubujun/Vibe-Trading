"""Volume-confirmed reversal: high volume and negative returns are long candidates."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_surge_reversal",
    "nickname": "放量反转",
    "theme": ["volume", "reversal"],
    "formula_latex": r"\mathrm{rank}\left(-r_t\right) \times \mathrm{rank}\left(\frac{\mathrm{volume}_t}{\mathrm{Mean}_{20}(\mathrm{volume}_t)}\right)",
    "columns_required": ["volume", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 20,
    "notes": "Volume surge combined with recent negative return; seeks short-term reversal after panic selling.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret = safe_div(delta(close, 1), close - delta(close, 1))
    vol_surge = safe_div(volume, ts_mean(volume, 20))
    return rank(-ret) * rank(vol_surge)