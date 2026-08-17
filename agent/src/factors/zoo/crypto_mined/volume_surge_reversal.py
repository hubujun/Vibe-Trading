"""Crypto factor: volume-surge reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, safe_div, signed_power, ts_mean, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_surge_reversal",
    "nickname": "VolumeSurgeReversal",
    "theme": ["volume", "reversal"],
    "formula_latex": "Z\\left(-\\mathrm{sign}(r_5)\\,|r_5|^2 \\cdot Z(\\bar v_5/\\bar v_{20})\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 25,
    "notes": "Contrarian signal scaling recent five-bar returns by a relative volume surge.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    rel_vol = safe_div(ts_mean(volume, 5), ts_mean(volume, 20))
    vol_tilt = zscore(rel_vol)

    ret5 = safe_div(delta(close, 5), close.shift(5))
    reversal_cue = -signed_power(ret5, 2)

    factor = decay_linear(zscore(vol_tilt * reversal_cue), 3)

    return factor.reindex(index=close.index, columns=close.columns)