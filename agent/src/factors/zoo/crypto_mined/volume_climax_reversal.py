"""crypto VOLUME: volume climax reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_climax_reversal",
    "nickname": "量峰反转",
    "theme": ["volume"],
    "formula_latex": "- \\mathrm{rank}_{20}(v_t) \\times \\frac{c_t - c_{t-3}}{c_{t-3}}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 20,
    "notes": "Negative recent return weighted by time-series volume rank; high-volume selloff may reverse.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume climax reversal factor."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    volume_rank = ts_rank(volume, 20)
    ret_3 = safe_div(delta(close, 3), close.shift(3))
    return -1.0 * volume_rank * ret_3