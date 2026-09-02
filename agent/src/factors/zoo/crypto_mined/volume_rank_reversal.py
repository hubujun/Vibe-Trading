"""crypto volume: high-volume price reversal."""

import pandas as pd

from src.factors.base import delta, rank, safe_div, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_volume_rank_reversal",
    "nickname": "放量反转",
    "theme": ["volume"],
    "formula_latex": "-\\mathrm{ts\\_rank}\\left(R_t \\times \\mathrm{rank}(V_t), 10\\right)",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 2,
    "min_warmup_bars": 14,
    "notes": "R_t = (C_t - C_{t-1}) / C_{t-1}. Interacts returns with cross-sectional volume rank; negative values favor recently strong-volume winners to revert.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)
    volume = panel["volume"].reindex(index=close.index, columns=close.columns).astype(float)

    ret = safe_div(delta(close, 1), close - delta(close, 1))
    vol_rank = rank(volume)
    return -ts_rank(ret * vol_rank, 10)