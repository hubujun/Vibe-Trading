"""crypto VOLUME: volume dry-up ahead of a range breakout."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_dryup_breakout",
    "nickname": "缩量突破",
    "theme": ["volume"],
    "formula_latex": r"-\mathrm{ts\_rank}_{60}\!\left(\frac{\mu_5(V)}{\mu_{30}(V)}\right)\times\left(\mathrm{ts\_rank}_{20}(C)-0.5\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 90,
    "notes": "Short-term volume relative to a 30-bar baseline, ranked against its own "
             "60-bar history and negated, so the score is high when participation has "
             "dried up; multiplied by the position of close inside its 20-bar range. "
             "Quiet accumulation near the top of the range is treated as a pre-breakout "
             "signature, while low-volume drift near the lows scores near zero.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-contraction breakout score, aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    vol_ratio = safe_div(ts_mean(volume, 5), ts_mean(volume, 30))
    dryness = -ts_rank(vol_ratio, 60)
    range_position = ts_rank(close, 20) - 0.5

    raw = dryness * range_position
    return zscore(raw)