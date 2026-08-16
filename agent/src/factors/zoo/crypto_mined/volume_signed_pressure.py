"""Volume-signed price pressure factor."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, signed_power, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_volume_signed_pressure",
    "nickname": "SignedVolumePressure",
    "theme": ["volume"],
    "formula_latex": r"\frac{\mathrm{ts\_mean}_{20}(\mathrm{sgn}(\Delta \mathrm{close}_t) \cdot \mathrm{volume}_t)}{\mathrm{ts\_mean}_{20}(\mathrm{volume}_t)}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "Volume weighted by the sign of the one-bar close move, normalised by average volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float).reindex(index=close.index, columns=close.columns)

    direction = signed_power(delta(close, 1), 0)
    signed_volume = direction * volume

    return safe_div(
        ts_mean(signed_volume, 20),
        ts_mean(volume, 20),
        eps=1e-12,
    )