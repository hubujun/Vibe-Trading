"""crypto volume/microstructure: volume-confirmed close location pressure."""

import pandas as pd

from src.factors.base import decay_linear, safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_mined_clv_volume_pressure",
    "nickname": "量价CLV压力",
    "theme": ["volume", "microstructure"],
    "formula_latex": "\\mathrm{decay}_5\\left(\\frac{2C-H-L}{H-L}\\cdot\\frac{C\\cdot V}{\\mathrm{MA}_{20}(C\\cdot V)}\\right)",
    "columns_required": ["close", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 21,
    "notes": "Volume-confirmed close location pressure; high when candle closes near high on above-average dollar volume.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    high = panel["high"].astype(float)
    low = panel["low"].astype(float)
    volume = panel["volume"].astype(float)

    hl = high - low
    clv = safe_div(2.0 * close - high - low, hl)
    dollar_volume = close * volume
    rel_volume = safe_div(dollar_volume, ts_mean(dollar_volume, 20))
    pressure = clv * rel_volume
    return decay_linear(pressure, 5)