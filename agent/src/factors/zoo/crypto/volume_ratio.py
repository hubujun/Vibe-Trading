# ============================================================
# 中文名称: 24h量比
# 简要说明: 当日成交量 / 过去7日均成交量。>1 表示放量，<1 表示缩量。
# 典型用途: 放量突破/下跌更有持续性；缩量盘整常预示方向选择。
# ============================================================
"""crypto VOLUME: daily volume vs 7-day average volume ratio."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, ts_mean

__alpha_meta__ = {
    "id": "crypto_volume_ratio",
    "nickname": "24h量比",
    "theme": ["volume"],
    "formula_latex": (
        "\\frac{\\mathrm{volume}_t}"
        "{\\mathrm{MA}(\\mathrm{volume}, 7)}"
    ),
    "columns_required": ["volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 7,
    "min_warmup_bars": 7,
    "notes": "Volume ratio vs 7-day MA. >1 = expanding volume, <1 = contracting.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return daily volume / 7-day MA volume ratio."""
    vol = panel["volume"].astype(float)
    vol_ma7 = ts_mean(vol, 7)
    return safe_div(vol, vol_ma7 + 1e-12)
