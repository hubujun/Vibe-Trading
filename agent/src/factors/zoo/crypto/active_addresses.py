# ============================================================
# 中文名称: 活跃地址动量
# 简要说明: 活跃地址数的30日均值变动率，(MA30_t - MA30_{t-30}) / MA30_{t-30}。
# 典型用途: 活跃地址加速增长 = 网络扩张 = 基本面改善信号。
# ============================================================
"""crypto MOMENTUM: active address 30-day MA rate of change."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_active_addresses",
    "nickname": "活跃地址动量",
    "theme": ["momentum"],
    "formula_latex": (
        r"\frac{\mathrm{MA}(\mathrm{addr}, 30)_t"
        r" - \mathrm{MA}(\mathrm{addr}, 30)_{t-30}}"
        r"{\mathrm{MA}(\mathrm{addr}, 30)_{t-30}}"
    ),
    "columns_required": ["onchain:active_addresses"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 30,
    "min_warmup_bars": 60,
    "notes": (
        "30-day MA of active addresses, change over the prior 30-day MA. "
        "Accelerating network activity = fundamental momentum."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return active address momentum: (MA30 - MA30_{t-30}) / MA30_{t-30}."""
    addr = panel["onchain:active_addresses"].astype(float)
    ma30 = ts_mean(addr, 30)
    prev_ma30 = ma30.shift(30)
    return safe_div(ma30 - prev_ma30, prev_ma30.abs() + 1e-12)
