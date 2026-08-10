"""crypto_mined_microstructure_volume_body_wick: volume-confirmed candle body/wick imbalance."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, signed_power, ts_mean, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_microstructure_volume_body_wick",
    "nickname": "量能K线形态",
    "theme": ["microstructure"],
    "formula_latex": "B_t = \\frac{C_t - O_t}{H_t - L_t},\\quad W_t = \\frac{2C_t - H_t - L_t}{H_t - L_t},\\quad Z_{V,t} = \\frac{V_t - \\mathrm{mean}_{20}(V_t)}{\\mathrm{std}_{20}(V_t)},\\quad F_t = \\mathrm{rank}(\\mathrm{decay\\_linear}((B_t + W_t) \\cdot \\mathrm{signed\\_power}(Z_{V,t}, 0.5), 5))",
    "columns_required": ["open", "high", "low", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 24,
    "notes": "Combines normalized candle body and close position within the high-low range, then scales by a 20-bar volume z-score. High volume confirms the microstructure pressure.",
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return volume-confirmed candle body/range position factor aligned to close."""
    close = panel["close"].astype(float)
    open_price = panel["open"].astype(float).reindex_like(close)
    high = panel["high"].astype(float).reindex_like(close)
    low = panel["low"].astype(float).reindex_like(close)
    volume = panel["volume"].astype(float).reindex_like(close)

    body_frac = safe_div(close - open_price, high - low)
    wick_balance = safe_div(2.0 * close - high - low, high - low)
    micro = body_frac + wick_balance

    vol_z = safe_div(volume - ts_mean(volume, 20), ts_std(volume, 20))
    raw = micro * signed_power(vol_z, 0.5)

    return rank(decay_linear(raw, 5))