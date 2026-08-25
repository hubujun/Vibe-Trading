"""crypto_mined volume reversal signal: high-volume short-term reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, rank, signed_power, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_reversal_signal",
    "nickname": "量增反转",
    "theme": ["volume"],
    "formula_latex": "\\mathrm{rank}\\left(-\\mathrm{ts\\_rank}(V,10) \\cdot \\mathrm{sign}\\left(\\Delta_5 P\\right)\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 11,
    "notes": "Ranks high-volume short-term price moves in the contrarian direction; uses sign of 5-day price change.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    vol_rank = ts_rank(volume, 10)
    price_change = delta(close, 5)
    sign_change = signed_power(price_change, 0)

    reversal = -1.0 * vol_rank * sign_change

    return rank(reversal)