from __future__ import annotations
# auto-injected imports (factor_miner)
from src.factors.base import rank, zscore, ts_rank, ts_corr, ts_cov, ts_mean, ts_std, ts_max, ts_min, ts_argmax, ts_argmin, delta, decay_linear, safe_div, signed_power, scale, vwap

"""Cross-sectional crypto alpha: volume-confirmed volatility compression breakout potential."""


import pandas as pd


__alpha_meta__ = {
    "id": "crypto_mined_vol_compression_volume_asymmetry",
    "nickname": "Vol Compression x Volume Asymmetry",
    "theme": ["volatility", "volume", "microstructure"],
    "formula_latex": r"\mathrm{rank}\left( -\mathrm{zscore}\left(\frac{ts\_std(r,5)}{ts\_std(r,20)}\right) \cdot \mathrm{rank}\left(\frac{\sum_{t=1}^{5} V_t \cdot \mathbb{1}[r_t>0]}{\sum_{t=1}^{5} V_t \cdot \mathbb{1}[r_t<0] + \epsilon}\right) \right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Volatility compression (short/long realized vol ratio) combined with up/down volume asymmetry. Low vol compression + buy-side volume dominance => upside breakout candidate. Market neutral via cross-sectional ranking.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    prev_close = close.shift(1)
    ret = safe_div(close - prev_close, prev_close)

    # Realized volatility compression: short-window vs long-window vol
    vol_short = ts_std(ret, 5)
    vol_long = ts_std(ret, 20)
    vol_ratio = safe_div(vol_short, vol_long + 1e-9)
    compression = -zscore(vol_ratio)

    # Volume asymmetry: buy-side volume vs sell-side volume over 5 bars
    up_mask = (ret > 0).astype(float)
    down_mask = (ret < 0).astype(float)
    up_vol = ts_mean(volume * up_mask, 5)
    down_vol = ts_mean(volume * down_mask, 5)
    vol_asym = safe_div(up_vol, down_vol + 1e-9)
    vol_asym_rank = rank(vol_asym)

    raw = compression * vol_asym_rank
    return rank(raw)