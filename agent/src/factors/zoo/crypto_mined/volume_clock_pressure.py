"""crypto VOLUME: volume-clocked directional pressure accumulation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import (
    delta,
    rank,
    safe_div,
    signed_power,
    ts_mean,
    ts_std,
)

__alpha_meta__ = {
    "id": "crypto_mined_volume_clock_pressure",
    "nickname": "量钟定向压力",
    "theme": ["volume"],
    "formula_latex": (
        r"\mathrm{rank}\left(\mathrm{mean}_{20}\left["
        r"\frac{V_t-\bar V_{60}}{\sigma_{60}(V)}\cdot"
        r"\mathrm{sgn}(r_t)\sqrt{\left|r_t/\sigma_{20}(r)\right|}\right]\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 20,
    "min_warmup_bars": 80,
    "notes": (
        "Volume-clock pressure: each bar's standardized return (compressed by a "
        "square-root signed power so a few spikes cannot dominate) is weighted by "
        "how unusual that bar's volume is versus its own 60-bar distribution. "
        "The 20-bar average measures persistent accumulation (high volume on up "
        "moves) or distribution (high volume on down moves). Aggregated across "
        "the window and cross-sectionally ranked."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-clocked directional pressure factor."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    ret_scaled = safe_div(ret, ts_std(ret, 20))

    vol_intensity = safe_div(
        volume - ts_mean(volume, 60),
        ts_std(volume, 60),
    )

    pressure = vol_intensity * signed_power(ret_scaled, 0.5)
    raw = ts_mean(pressure, 20)
    return rank(raw)