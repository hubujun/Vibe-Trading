"""crypto mined VOLUME: volume flow vs return correlation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import (
    delta,
    rank,
    safe_div,
    ts_corr,
)

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_return_corr",
    "nickname": "VolumeFlowReturnCorr",
    "theme": ["volume"],
    "formula_latex": "\\text{rank}\\left(\\rho_{15}\\left(\\frac{\\Delta_1 P}{P_{t-1}}, \\frac{\\Delta_1 V}{V_{t-1}}\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 16,
    "notes": "Cross-sectional rank of rolling correlation between daily returns and daily volume changes; high rank identifies volume-confirmed price moves.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret_1 = safe_div(delta(close, 1), close.shift(1))
    volume_change_1 = safe_div(delta(volume, 1), volume.shift(1))
    flow_corr = ts_corr(ret_1, volume_change_1, 15)

    return rank(flow_corr)