"""Crypto mined factor: volume-flow and price-flow correlation quality."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_flow_quality",
    "nickname": "VolumeFlowQuality",
    "theme": ["volume", "momentum", "quality"],
    "formula_latex": "\\operatorname{rank}_{cs}\\left(\\operatorname{decay\\_linear}_{10}\\left(\\operatorname{zscore}_{cs}\\left(\\rho_{20}\\left(\\frac{\\Delta V_t}{V_{t-1}}, \\frac{\\Delta C_t}{C_{t-1}}\\right)\\right)\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 30,
    "notes": "Ranks cross-sectional persistence of positive volume-flow/price-flow correlation.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    volume_growth = safe_div(delta(volume, 1), volume.shift(1))

    corr = ts_corr(volume_growth, ret, 20)
    corr_z = zscore(corr)
    flow_quality = decay_linear(corr_z, 10)

    return rank(flow_quality)