"""crypto volume: volume-pain reversal after sharp drawdowns."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_pain_reversal",
    "nickname": "VolumePainReversal",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{pain}_t = -\\mathrm{mean}_{5}(\\Delta C_t); \\; f_t = \\mathrm{rank}\\left(\\mathrm{pain}_t \\cdot \\mathrm{ts\\_rank}_{20}(V_t)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 7,
    "min_warmup_bars": 20,
    "notes": "Fades recent drawdowns when volume participation is high.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    one_bar_return = delta(close, 1)
    recent_loss = -ts_mean(one_bar_return, 5)
    volume_rank = ts_rank(volume, 20)

    return rank(recent_loss * volume_rank)