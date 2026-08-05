# ============================================================
# 中文名称: OI-价格背离
# 简要说明: 检测价格变动方向与OI变动方向不一致时的信号强度。
#   sign(price_change) != sign(oi_change) 时输出 |oi_change * price_change|，
#   否则输出 0。
# 典型用途: 价格上涨但OI下降 = 上涨乏力，下跌但OI上升 = 空头控盘。
# ============================================================
"""crypto SENTIMENT: OI-price divergence signal strength."""

from __future__ import annotations

import numpy as np
import pandas as pd

__alpha_meta__ = {
    "id": "crypto_oi_price_divergence",
    "nickname": "OI-价格背离",
    "theme": ["sentiment"],
    "formula_latex": (
        "\\begin{cases}"
        "|\\Delta\\mathrm{OI} \\cdot \\Delta\\mathrm{price}|, "
        "& \\mathrm{sign}(\\Delta\\mathrm{price}) \\neq "
        "\\mathrm{sign}(\\Delta\\mathrm{OI}) \\\\"
        "0, & \\text{otherwise}"
        "\\end{cases}"
    ),
    "columns_required": ["oi", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 2,
    "notes": (
        "Non-zero when price and OI move in opposite directions. "
        "Strong divergence signals potential reversal."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return OI-price divergence indicator (0 when in sync, >0 when diverging).

    Relies on the panel loader to supply multi-row OI history (CCXT).  When
    only a single OI snapshot is available the loader omits OI so the registry
    raises SkipAlpha rather than producing an all-zero IC series (LAO-47).
    """
    close = panel["close"].astype(float)
    oi = panel["oi"].astype(float)
    # Align to the close index via forward-fill (OI history may be sparser
    # than daily OHLCV).
    oi_aligned = oi.reindex(index=close.index, method="ffill")

    oi_change = oi_aligned.diff()
    price_change = close.diff()

    div_sign = np.sign(oi_change) != np.sign(price_change)
    strength = (oi_change.abs() * price_change.abs()).fillna(0.0)

    result = pd.DataFrame(
        np.where(div_sign, strength, 0.0),
        index=close.index,
        columns=close.columns,
    )
    return result
