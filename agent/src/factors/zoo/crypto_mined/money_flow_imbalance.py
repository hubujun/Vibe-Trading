"""crypto VOLUME: decay-weighted money-flow imbalance (close location value x volume)."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div

__alpha_meta__ = {
    "id": "crypto_mined_money_flow_imbalance",
    "nickname": "衰减资金流不平衡",
    "theme": ["volume"],
    "formula_latex": (
        "\\frac{\\sum_{k=0}^{n-1} w_k\\, \\mathrm{CLV}_{t-k}\\, V_{t-k}}"
        "{\\sum_{k=0}^{n-1} w_k\\, V_{t-k}}, \\quad "
        "\\mathrm{CLV}_t = \\frac{2\\,(\\mathrm{close}_t - \\mathrm{low}_t)}"
        "{\\mathrm{high}_t - \\mathrm{low}_t} - 1"
    ),
    "columns_required": ["high", "low", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 10,
    "notes": (
        "Volume-weighted close location value: how close each bar closed to its high "
        "versus its low, weighted by that bar's share of linearly decayed volume. "
        "Bounded in [-1, 1]; positive readings mean recent turnover is concentrated in "
        "bars that closed near their highs (buying pressure), negative readings the "
        "opposite. NaN bars (flat high==low) propagate rather than being filled."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the decay-weighted, volume-share weighted close location value."""
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    bar_range = high - low
    clv = 2.0 * safe_div(close - low, bar_range) - 1.0

    num = decay_linear(clv * volume, 10)
    den = decay_linear(volume, 10)
    return safe_div(num, den)