"""market regime momentum: momentum scaled by BTC market regime.

事件冲击的动量代理 — BTC 动量强 (risk-on) 时放大动量因子;
BTC 动量弱/负 (risk-off) 时缩小动量暴露 (防御, 不反转).

思想: 重大宏观事件 (监管/放水/黑天鹅) 首先体现在 BTC 动量和波动率上,
用可回测的市场状态对横截面动量做条件化 — 事件行情中追强势 vs 防御的权衡.
"""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, scale, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_market_regime_momentum",
    "nickname": "MarketRegimeMomentum",
    "theme": ["regime", "momentum"],
    "formula_latex": r"\mathrm{scale}\left(\mathrm{rank}_{20}(\Delta C/C)\cdot\left(1+4\cdot\mathrm{rank}(mom_{BTC,20})\right)\right)",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 30,
    "notes": "Momentum factor conditioned on BTC 20d momentum (regime proxy). In risk-on (BTC strong) momentum is amplified; in risk-off it is damped (defensive tilt).",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)

    if "BTC-USDT" in close.columns:
        btc = close["BTC-USDT"]
    else:
        btc = close.mean(axis=1)  # 无 BTC 列时用全市场均值代理

    btc_mom = btc.pct_change(20).fillna(0.0)
    # 动量: 每币 20d 涨跌幅, 横截面 rank (0~1)
    per_coin_mom = close.pct_change(20).rank(axis=1, pct=True)
    # BTC 制度: rank 到 0~1 (强=1), 乘数 1 + 4×regime (1~5 倍)
    regime_rank = btc_mom.rank(pct=True).fillna(0.5)
    multiplier = 1.0 + 4.0 * regime_rank
    score = per_coin_mom.mul(multiplier, axis=0)
    return decay_linear(scale(score), 5)
