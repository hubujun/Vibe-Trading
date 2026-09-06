"""Volume acceleration trend with intraday directional confirmation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, signed_power, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_acceleration_trend",
    "nickname": "量能加速方向",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{rank}\left( \frac{\mathrm{mean}_5(V)-\mathrm{mean}_{20}(V)}{\mathrm{std}_{20}(V)} \cdot \mathrm{sgn}\left(\frac{C-O}{O}\right) \sqrt{\left|\frac{C-O}{O}\right|} \right)",
    "columns_required": ["close", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 20,
    "notes": "Signs recent volume acceleration by the size and direction of the intraday open-to-close move.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    open_ = panel["open"].astype(float)
    volume = panel["volume"].astype(float)

    short_volume = ts_mean(volume, 5)
    long_volume = ts_mean(volume, 20)
    volume_std = ts_std(volume, 20)

    volume_accel = safe_div(short_volume - long_volume, volume_std + 1e-12)
    intraday_ret = safe_div(close - open_, open_)
    signed_ret = signed_power(intraday_ret, 0.5)

    return rank(volume_accel * signed_ret)