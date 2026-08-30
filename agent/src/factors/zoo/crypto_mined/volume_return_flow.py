"""crypto_mined_volume_return_flow: volume-return concordance scaled by volume rank."""

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_corr, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_return_flow",
    "nickname": "VolumeReturnFlow",
    "theme": ["volume"],
    "formula_latex": "\\operatorname{rank}\\left(\\operatorname{ts\\_mean}_{5}\\left(\\operatorname{ts\\_corr}_{10}\\left(V,\\frac{\\Delta C}{C_{-1}}\\right)\\times \\operatorname{ts\\_rank}_{20}(V)\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 30,
    "notes": "Measures whether volume flows in the same direction as price changes, weighted by recent volume activity.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    corr = ts_corr(volume, ret, 10)
    vol_rank = ts_rank(volume, 20)
    flow = corr * vol_rank
    smoothed = ts_mean(flow, 5)

    return rank(smoothed)