# ============================================================
# 中文名称: 永续资金费率
# 简要说明: 永续合约资金费率原始值。正数表示多头向空头支付，通常反映多头过热。
# 典型用途: 极端的正/负资金费率可作为反向指标。
# ============================================================
"""crypto CARRY: raw perpetual swap funding rate."""

from __future__ import annotations

import pandas as pd

__alpha_meta__ = {
    "id": "crypto_funding_rate",
    "nickname": "永续资金费率",
    "theme": ["carry"],
    "formula_latex": "\\mathrm{funding\\_rate}",
    "columns_required": ["funding_rate"],
    "universe": ["crypto"],
    "frequency": ["8h", "1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 1,
    "notes": "Raw perpetual swap funding rate. Positive = longs pay shorts (overheated).",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the raw funding rate series aligned to close panel index."""
    fr = panel["funding_rate"].astype(float)
    close = panel["close"]
    # funding_rate panel may have a different frequency (e.g. 8h) than the
    # daily close panel — forward-fill into the close index so output shape
    # matches expectations.
    aligned = fr.reindex(index=close.index, method="ffill")
    # Drop any dates before the first funding_rate observation (NaN rows).
    first_valid = fr.index.min()
    aligned.loc[aligned.index < first_valid] = None  # type: ignore[union-attr]
    return aligned
