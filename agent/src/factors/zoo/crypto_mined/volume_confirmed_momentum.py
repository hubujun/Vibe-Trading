"""Volume-confirmed momentum: volume-price correlation plus short-term momentum."""

import numpy as np
import pandas as pd

from src.factors.base import decay_linear, delta, rank, ts_corr

__alpha_meta__ = {
    "id": "crypto_mined_volume_confirmed_momentum",
    "nickname": "VolumeConfirmedMomentum",
    "theme": ["momentum", "volume"],
    "formula_latex": "\\mathrm{rank}\\left(\\mathrm{ts\\_corr}(\\Delta \\log C, V, 10)\\right) + \\mathrm{rank}\\left(\\mathrm{decay\\_linear}(\\Delta \\log C, 5)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 15,
    "notes": "Ranks assets with positively correlated return-volume flow and strong recent trend.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    log_ret = delta(np.log(close), 1)
    ret_volume_corr = ts_corr(log_ret, volume, 10)
    recent_momentum = decay_linear(log_ret, 5)

    return rank(ret_volume_corr) + rank(recent_momentum)