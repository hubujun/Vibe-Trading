"""crypto VOLUME: high-volume short-term reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_climax_reversal",
    "nickname": "成交量高潮反转",
    "theme": ["volume"],
    "formula_latex": "z\\left(-\\mathrm{ret}_{5} \\times \\mathrm{ts\\_rank}(\\mathrm{volume}, 20)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 20,
    "notes": "Short-term reversal weighted by recent volume rank; volume climax amplifies reversal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return z-scored negative 5-bar return amplified by volume rank."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)
    ret5 = safe_div(close, close.shift(5)) - 1.0
    vol_rank = ts_rank(volume, 20)
    raw = -ret5 * vol_rank
    return zscore(raw)