"""Bullish/bearish volume imbalance scaled by rolling volume conviction."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_bull_bear_volume_imbalance",
    "nickname": "BullBearVolumeImbalance",
    "theme": ["volume", "sentiment"],
    "formula_latex": r"rank_t\left(\mathrm{ts\_rank}_{20}(V_t)\cdot \frac{T_{10}(V_t \mathbf{1}_{C_t \ge O_t}) - T_{10}(V_t \mathbf{1}_{C_t < O_t})}{T_{10}(V_t \mathbf{1}_{C_t \ge O_t}) + T_{10}(V_t \mathbf{1}_{C_t < O_t})}\right)",
    "columns_required": ["close", "open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 1,
    "min_warmup_bars": 20,
    "notes": "Measures whether trailing volume is concentrated on up-bars versus down-bars; rolling volume percentile is used as conviction.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    open_ = panel["open"].astype(float)
    volume = panel["volume"].astype(float)

    up_day = (close >= open_).astype(float)
    down_day = (close < open_).astype(float)

    up_volume = ts_mean(volume * up_day, 10)
    down_volume = ts_mean(volume * down_day, 10)

    imbalance = safe_div(up_volume - down_volume, up_volume + down_volume)
    conviction = ts_rank(volume, 20)

    return rank(imbalance * conviction)