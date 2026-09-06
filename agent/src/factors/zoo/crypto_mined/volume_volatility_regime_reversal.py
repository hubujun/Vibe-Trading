"""crypto volume: volatility-regime volume climax reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_volatility_regime_reversal",
    "nickname": "VolumeVolRegimeReversal",
    "theme": ["volume"],
    "formula_latex": "-\\left(\\mathrm{rank}(\\mathrm{ts\\_mean}_{5}(r)) - 0.5\\right) \\times \\mathrm{rank}\\left(\\frac{V}{\\mathrm{ts\\_mean}_{20}(V)}\\right) \\times \\mathrm{rank}\\left(\\frac{\\mathrm{ts\\_std}_{5}(r)}{\\mathrm{ts\\_std}_{20}(r)}\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 21,
    "notes": "Fades short-window winners when both volume and short-horizon volatility expand relative to their baselines.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume/volatility regime reversal signal."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close)

    short_vol = ts_std(ret, 5)
    long_vol = ts_std(ret, 20)
    vol_regime = safe_div(short_vol, long_vol)

    short_mom = ts_mean(ret, 5)
    relative_volume = safe_div(volume, ts_mean(volume, 20))

    return -1.0 * (rank(short_mom) - 0.5) * rank(relative_volume) * rank(vol_regime)