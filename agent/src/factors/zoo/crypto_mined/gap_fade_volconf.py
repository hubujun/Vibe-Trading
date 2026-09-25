"""crypto REVERSAL: overnight gap fade with volume confirmation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_gap_fade_volconf",
    "nickname": "隔夜跳空回补",
    "theme": ["reversal"],
    "formula_latex": (
        "-\\mathrm{ts\\_rank}\\left(\\mathrm{decay}_5"
        "\\left(\\frac{O_t - C_{t-1}}{C_{t-1}}\\right), 20\\right)"
        "\\cdot \\mathrm{ts\\_rank}\\left(\\frac{V_t}{\\bar{V}_{20}}, 20\\right)"
    ),
    "columns_required": ["open", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
    "notes": (
        "Overnight gap (open vs prior close) is decay-weighted over 5 bars and "
        "rolling-rank normalised over 20 bars; the sign is flipped to express "
        "mean-reversion of the gap. Relative volume rank acts as a conviction "
        "multiplier so that gaps traded on heavy volume are faded hardest."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the volume-confirmed overnight gap fade score."""
    open_ = panel["open"].astype(float)
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    prev_close = close.shift(1)
    gap = safe_div(open_ - prev_close, prev_close)

    gap_signal = decay_linear(gap, 5)
    gap_rank = ts_rank(gap_signal, 20)

    rel_volume = safe_div(volume, ts_mean(volume, 20))
    vol_rank = ts_rank(rel_volume, 20)

    return -gap_rank * vol_rank