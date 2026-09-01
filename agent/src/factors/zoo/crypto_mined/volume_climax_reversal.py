"""crypto VOLUME: volume climax price reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, delta, rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_climax_reversal",
    "nickname": "VolumeClimaxReversal",
    "theme": ["volume"],
    "formula_latex": "\\text{rank}_t\\left(\\frac{\\text{volume}}{\\text{ts\\_mean}_{20}(\\text{volume})}\\right) \\times \\text{rank}_t\\left(-\\frac{\\Delta \\text{close}}{\\text{close}_{t-1}}\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 20,
    "notes": "High relative volume combined with recent price declines captures climax selling reversal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-climax reversal signal."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, method="ffill")

    volume_ratio = safe_div(volume, ts_mean(volume, 20))
    ret = safe_div(delta(close, 1), close.shift(1))
    return rank(volume_ratio) * rank(-ret)