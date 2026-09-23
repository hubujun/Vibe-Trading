"""crypto VOLUME: volume vs absolute-return rank divergence (absorption)."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_absorption",
    "nickname": "量价吸收",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{ts\\_rank}(V,20) - \\mathrm{ts\\_rank}(|r_t|,20)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "High traded volume accompanied by small absolute return signals absorption/accumulation; compares rolling rank of volume against rolling rank of absolute 1-bar return.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-minus-move rank divergence, aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    vol_rank = ts_rank(volume, 20)
    move_rank = ts_rank(ret.abs(), 20)

    return vol_rank - move_rank