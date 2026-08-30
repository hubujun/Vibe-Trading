"""crypto VOLUME: volume-weighted signed cubic return skew."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, rank, safe_div, signed_power

__alpha_meta__ = {
    "id": "crypto_mined_volume_weighted_return_skew",
    "nickname": "量加权收益偏度",
    "theme": ["volume"],
    "formula_latex": "\\operatorname{rank}_{cs}\\left(\\frac{\\mathrm{DW}_{20}(v_t \\cdot \\mathrm{sgn}(r_t)|r_t|^3)}{\\mathrm{DW}_{20}(v_t)}\\right), \\quad r_t = \\frac{\\Delta \\mathrm{close}_t}{\\mathrm{close}_t}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 21,
    "notes": "Volume-weighted skewness of recent returns; higher rank means large volume is associated with up moves.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex_like(close)

    ret = safe_div(delta(close, 1), close)
    ret3 = signed_power(ret, 3)

    weighted_ret3 = decay_linear(volume * ret3, 20)
    weighted_volume = decay_linear(volume, 20)
    factor = safe_div(weighted_ret3, weighted_volume)

    return rank(factor)