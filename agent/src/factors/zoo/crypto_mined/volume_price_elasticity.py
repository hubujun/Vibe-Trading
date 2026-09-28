"""crypto VOLUME: volume-price elasticity (rolling beta of returns on volume change)."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, rank, safe_div, ts_cov, ts_std

__alpha_meta__ = {
    "id": "crypto_mined_volume_price_elasticity",
    "nickname": "量价弹性",
    "theme": ["volume"],
    "formula_latex": (
        "-\\mathrm{rank}\\left(\\mathrm{decay}_3\\left("
        "\\frac{\\mathrm{cov}_{30}\\!\\left(r_t,\\; \\Delta V_t / V_{t-1}\\right)}"
        "{\\sigma^2_{30}\\!\\left(\\Delta V_t / V_{t-1}\\right)}\\right)\\right)"
    ),
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 31,
    "notes": (
        "Rolling 30-bar beta of simple returns on relative volume changes, i.e. "
        "how many return units one unit of volume surprise buys. High elasticity "
        "means price is easily pushed by flow (fragile, crowded book) and is "
        "penalised; low elasticity means the same flow barely moves price "
        "(absorbed by real supply/demand) and is rewarded. The beta is decay "
        "smoothed and flipped in sign, then cross-sectionally ranked."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the cross-sectionally ranked negative volume-price elasticity."""
    close = panel["close"].astype(float)
    volume = (
        panel["volume"]
        .astype(float)
        .reindex(index=close.index, columns=close.columns, method="ffill")
    )

    ret = safe_div(close, close.shift(1)) - 1.0
    vol_chg = safe_div(volume, volume.shift(1)) - 1.0

    beta = safe_div(ts_cov(ret, vol_chg, 30), ts_std(vol_chg, 30) ** 2)
    smoothed = decay_linear(beta, 3)
    return -rank(smoothed)