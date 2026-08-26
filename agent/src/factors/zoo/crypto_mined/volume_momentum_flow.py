"""crypto mined volume momentum flow: volume-ranked confirmation of short price moves."""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, delta, scale, signed_power, ts_corr, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_momentum_flow",
    "nickname": "VolumeMomentumFlow",
    "theme": ["volume"],
    "formula_latex": r"\mathrm{scale}\left(\mathrm{rank}_{20}(V_t)\cdot\mathrm{sgn}(\rho_{20}(V_t,\Delta C_t))|\rho_{20}|^2\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 10,
    "min_warmup_bars": 30,
    "notes": "Ranks volume and multiplies by squared volume-price correlation to focus on high-volume, directionally consistent price flow.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return a volume-confirmed price flow signal aligned to the close index."""
    close = panel["close"].astype(float)
    volume = panel["volume"].astype(float)

    price_move = delta(close, 1)
    volume_percentile = ts_rank(volume, 20)
    vol_price_corr = ts_corr(volume, price_move, 20)

    flow_score = volume_percentile * signed_power(vol_price_corr, 2.0)
    return decay_linear(scale(flow_score), 10)