"""crypto LIQUIDITY: Amihud illiquidity shock reversal."""

from __future__ import annotations

import pandas as pd

from src.factors.base import delta, safe_div, ts_mean, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_amihud_illiquidity_shock",
    "nickname": "Amihud冲击反转",
    "theme": ["liquidity", "microstructure"],
    "formula_latex": r"-\mathrm{ts\_rank}\left(\frac{\mathrm{ts\_mean}\left(\frac{|\Delta close|}{volume},5\right)}{\mathrm{ts\_mean}\left(\frac{|\Delta close|}{volume},20\right)}-1,30\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 55,
    "notes": "Amihud illiquidity is absolute return divided by volume; short-term shock versus 20-bar baseline, inversely ranked.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the negative rank of the Amihud illiquidity shock."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    abs_ret = abs(delta(close, 1))
    amihud = safe_div(abs_ret, volume)
    short = ts_mean(amihud, 5)
    long = ts_mean(amihud, 20)
    shock = safe_div(short, long) - 1.0
    return -ts_rank(shock, 30)