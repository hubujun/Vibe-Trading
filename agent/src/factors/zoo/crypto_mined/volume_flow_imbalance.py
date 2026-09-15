"""crypto VOLUME: signed volume flow imbalance (OBV-slope proxy).

Classical On-Balance-Volume cumulates signed volume, which is unbounded and
non-stationary. Here the same information is captured in a stationary way:
each bar's volume is signed by the direction of the close-to-close return and
normalised by a 20-bar volume baseline, then aggregated with a linearly
decayed moving average so that recent flow dominates.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_imbalance",
    "nickname": "签名成交量流不平衡",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{DECAY}_{10}\\!\\left[\\frac{\\mathrm{sign}(r_t)\\,V_t}{\\overline{V}_{20}}\\right]",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 30,
    "notes": (
        "Volume signed by the sign of the daily return, divided by the 20-bar "
        "mean volume, then linearly decay-weighted over 10 bars. A stationary "
        "stand-in for the slope of On-Balance-Volume, isolating whether "
        "turnover is flowing into up bars or down bars."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Decay-weighted signed volume flow, aligned to the close index."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(close, close.shift(1)) - 1.0
    direction = np.sign(ret)

    flow = direction * volume
    baseline = ts_mean(volume, 20)
    norm_flow = safe_div(flow, baseline)

    return decay_linear(norm_flow, 10)