"""crypto VOLATILITY: range-compression breakout with decaying confirmation."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_range_compression_breakout",
    "nickname": "波动压缩突破",
    "theme": ["volatility", "momentum"],
    "formula_latex": (
        "\\mathrm{decay}_5\\!\\left("
        "\\frac{\\,C_t / \\mathrm{MA}_{20}(C)_t - 1\\,}"
        "{\\frac{(H_t-L_t)/C_t}{\\mathrm{MA}_{20}\\!\\left((H-L)/C\\right)} + \\epsilon}"
        "\\right)"
    ),
    "columns_required": ["close", "high", "low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": (
        "Trend strength normalised by the ratio of the current intraday range to its "
        "20-bar average. When the range is compressed relative to its own history, a "
        "given trend move carries more information and is amplified; when the range is "
        "already expanded the signal is damped. Linearly decayed over 5 bars."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the range-compression-adjusted trend score aligned to close."""
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)

    hl_range = safe_div(high - low, close)
    range_baseline = ts_mean(hl_range, 20)
    compression = safe_div(hl_range, range_baseline)

    trend = safe_div(close, ts_mean(close, 20)) - 1.0

    raw = safe_div(trend, compression + 1e-12)
    return decay_linear(raw, 5)