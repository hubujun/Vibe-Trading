from __future__ import annotations
# auto-injected imports (factor_miner)
from src.factors.base import rank, zscore, ts_rank, ts_corr, ts_cov, ts_mean, ts_std, ts_max, ts_min, ts_argmax, ts_argmin, delta, decay_linear, safe_div, signed_power, scale, vwap

"""Crypto cross-sectional alpha: Volume-weighted momentum reversal via
volume-clock dispersion. Combines a short-term momentum signal with the
dispersion of volume across the cross-section, producing a market-neutral
factor that scales momentum by how 'unusual' a coin's recent volume is
relative to its own history, then cross-sectionally ranks the result."""


import pandas as pd



__alpha_meta__ = {
    "id": "crypto_mined_vol_clock_momentum_dispersion",
    "nickname": "Volume-Clock Momentum Dispersion",
    "theme": ["volume"],
    "formula_latex": (
        r"\mathrm{rank}\left( \frac{r_{5}}{\sigma_{20}(r)} \cdot "
        r"\left( \mathrm{ts\_rank}(V, 20) - \mathrm{ts\_rank}(V, 60) \right) \right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 65,
    "notes": (
        "Cross-sectional rank of risk-adjusted 5-day momentum multiplied by "
        "the change in short vs long volume rank. Volume acceleration amplifies "
        "momentum; volume deceleration flips it. Market-neutral via CS rank."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    # 5-day momentum
    mom5 = safe_div(close - close.shift(5), close.shift(5))

    # 20-day realized volatility of daily returns
    ret1 = safe_div(delta(close, 1), close.shift(1))
    vol20 = ts_std(ret1, 20)

    # Risk-adjusted momentum
    risk_adj_mom = safe_div(mom5, vol20)

    # Volume acceleration: short-term volume rank minus long-term volume rank
    vol_rank_short = ts_rank(volume, 20)
    vol_rank_long = ts_rank(volume, 60)
    vol_accel = vol_rank_short - vol_rank_long

    # Combined signal: momentum gated by volume acceleration
    signal = risk_adj_mom * vol_accel

    # Cross-sectional rank for market neutrality
    return rank(signal)