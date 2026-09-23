"""crypto VOLUME: signed volume-flow imbalance."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, signed_power, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_imbalance",
    "nickname": "方向性量能失衡",
    "theme": ["volume"],
    "formula_latex": "\\frac{\\mathrm{mean}_{20}\\left(V_t\\,\\mathrm{sgn}(r_t)|r_t|^{1/2}\\right)}{\\mathrm{mean}_{20}(V_t)}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "Volume weighted by return direction and square-rooted magnitude, averaged and normalised by average volume; measures the net directional volume flow (up-volume dominance vs down-volume dominance).",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the normalised signed volume-flow imbalance, aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    flow = volume * signed_power(ret, 0.5)

    return safe_div(ts_mean(flow, 20), ts_mean(volume, 20))