# ============================================================
# 中文名称: NVT比率
# 简要说明: 网络价值/交易量比率。高NVT = 价格相对于链上交易量被高估。
# 典型用途: NVT > 200 = 可能泡沫；NVT < 50 = 可能低估。与价格背离时信号更强。
# ============================================================
"""crypto VALUE: Network Value to Transactions (NVT) ratio."""

from __future__ import annotations

import pandas as pd

from src.factors.base import ts_mean

__alpha_meta__ = {
    "id": "crypto_nvt_ratio",
    "nickname": "NVT比率",
    "theme": ["value"],
    "formula_latex": r"\mathrm{NVT}_t",
    "columns_required": ["onchain:nvt"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 90,
    "min_warmup_bars": 90,
    "notes": (
        "NVT = Market Cap / Daily Transaction Volume. Like a P/E ratio for "
        "blockchains. High NVT = overvalued relative to utility; very low "
        "NVT = undervalued. Values > 200 historically indicate bubble "
        "territory for BTC."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return raw NVT ratio values."""
    nvt = panel["onchain:nvt"].astype(float)
    return nvt
