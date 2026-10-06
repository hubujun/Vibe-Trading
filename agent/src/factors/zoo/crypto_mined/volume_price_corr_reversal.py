"""crypto VOLUME: volume-price co-movement reversal filtered by volume volatility."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_corr, ts_mean, ts_std, zscore

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_corr_reversal",
    "nickname": "量价共振反转",
    "theme": ["volume"],
    "formula_latex": r"-\mathrm{corr}_{10}(\Delta C_t, \Delta V_t)\cdot \frac{\sigma_{20}(V_t)}{\mu_{20}(V_t)}",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": "Negative 10-bar correlation between price changes and volume changes, "
             "scaled by the 20-bar coefficient of variation of volume. When price moves "
             "are tightly confirmed by volume (high positive corr) in an otherwise noisy "
             "volume regime, the move is crowded and mean-reverts; the CV multiplier "
             "rewards signals that appear in genuinely volatile volume regimes.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-confirmation reversal score, aligned to close."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    d_close = delta(close, 1)
    d_volume = delta(volume, 1)

    co_move = ts_corr(d_close, d_volume, 10)
    vol_cv = safe_div(ts_std(volume, 20), ts_mean(volume, 20))

    raw = -co_move * vol_cv
    return zscore(raw)