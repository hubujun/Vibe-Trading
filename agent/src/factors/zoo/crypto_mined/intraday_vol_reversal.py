"""crypto MICROSTRUCTURE/REVERSAL: volume-gated intraday reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_intraday_vol_reversal",
    "nickname": "成交量门控日内反转",
    "theme": ["reversal", "microstructure"],
    "formula_latex": "-\\mathrm{zscore}(\\mathrm{ts\\_mean}(r^{intra}_{5}))\\cdot\\mathrm{ts\\_rank}(V_{20})",
    "columns_required": ["close", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 21,
    "notes": "Five-bar average intraday (close vs open) return, negated and cross-sectionally z-scored, weighted by the time-series rank of volume so that high-turnover moves reverse more strongly.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    open_ = panel["open"].astype(float)
    volume = panel["volume"].astype(float)

    intraday = safe_div(close - open_, open_)
    smoothed = ts_mean(intraday, 5)
    vol_rank = ts_rank(volume, 20)

    return -zscore(smoothed) * vol_rank