"""crypto VOLATILITY/VOLUME: fade co-movement between range and volume change."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, safe_div, ts_corr, zscore

__alpha_meta__ = {
    "id": "crypto_mined_range_volume_corr_fade",
    "nickname": "波量相关反转",
    "theme": ["volatility", "volume", "reversal"],
    "formula_latex": "z\\left(-\\mathrm{decay}_5\\left(\\mathrm{ts\\_corr}_{20}\\left(\\frac{H-L}{C},\\Delta V\\right)\\right)\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Fades regimes where volume spikes are accompanied by range expansion, a possible exhaustion signal.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].reindex_like(close).astype(float)
    low = panel["low"].reindex_like(close).astype(float)
    volume = panel["volume"].reindex_like(close).astype(float)

    range_pct = safe_div(high - low, close)
    volume_chg = delta(volume, 1)
    corr = ts_corr(range_pct, volume_chg, 20)
    return zscore(-decay_linear(corr, 5))