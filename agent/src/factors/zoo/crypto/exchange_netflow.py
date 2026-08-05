# ============================================================
# 中文名称: 交易所净流量
# 简要说明: 交易所净流入/流出（正值=净流入=潜在卖压，负值=净流出=持有信号）。
# 典型用途: 大幅净流入常出现在价格顶部（转入交易所准备卖出）；大幅净流出
#           常出现在积累阶段。
# ============================================================
"""crypto SENTIMENT: exchange net inflow/outflow (raw)."""

from __future__ import annotations

import pandas as pd

__alpha_meta__ = {
    "id": "crypto_exchange_netflow",
    "nickname": "交易所净流量",
    "theme": ["sentiment"],
    "formula_latex": r"\mathrm{ExchangeNetflow}_t",
    "columns_required": ["onchain:exchange_netflow"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 7,
    "min_warmup_bars": 1,
    "notes": (
        "Raw exchange net inflow/outflow. Positive = coins moving onto "
        "exchanges (bearish, potential sell pressure). Negative = coins "
        "moving off exchanges (bullish, accumulation signal)."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return raw exchange netflow (positive = inflow, negative = outflow)."""
    return panel["onchain:exchange_netflow"].astype(float)
