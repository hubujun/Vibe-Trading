"""Open-close volume-confirmed reversal factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_open_volume_reversal",
    "nickname": "open_volume_reversal",
    "theme": ["reversal", "microstructure"],
    "formula_latex": r"""F_t = -\mathrm{rank}\left( \frac{c_t - o_t}{o_t} \right) \cdot \mathrm{ts\_rank}\left( \frac{v_t}{\mathrm{ts\_mean}(v_t, 20)}, 5 \right)""",
    "columns_required": ["close", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 25,
    "notes": "Open-to-close reversal amplified by recent volume surge; no future data.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return open-to-close volume-confirmed reversal aligned to close."""
    close = panel["close"]
    open_px = panel["open"]
    volume = panel["volume"]

    open_ret = safe_div(close - open_px, open_px)
    vol_ratio = safe_div(volume, ts_mean(volume, 20))
    return -rank(open_ret) * ts_rank(vol_ratio, 5)