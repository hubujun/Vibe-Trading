"""Crypto volume-confirmed short-term momentum."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, safe_div, signed_power, ts_rank, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_surge_direction",
    "nickname": "量升方向",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{sign}(C-O) \cdot \mathrm{ts\_rank}_{20}(V) \cdot \mathrm{ts\_rank}_{10}(\Delta C / C_{-1})",
    "columns_required": ["close", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "High volume rank, positive intraday direction, and strong short-term return rank produce a long signal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    open_ = panel["open"].astype(float)
    volume = panel["volume"].astype(float)

    ret = safe_div(delta(close, 1), close.shift(1))
    direction = signed_power(safe_div(close - open_, open_), 0)
    raw = ts_rank(volume, 20) * direction * ts_rank(ret, 10)

    return zscore(decay_linear(raw, 5))