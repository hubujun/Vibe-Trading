"""market regime volatility: defensive tilt when BTC volatility spikes.

事件冲击的波动代理 — 重大事件后 BTC 波动率飙升 (冲击剧烈程度),
此时横截面偏向低波动币 (防御): score = -rank(vol) × btc_vol.

思想: 黑天鹅/监管冲击的第一反应是波动率放大, 高波动期低波动币
(质押/蓝筹) 相对抗跌 — 用可回测的 BTC 波动率条件化波动率因子.
"""

from __future__ import annotations

import pandas as pd

from src.factors.base import decay_linear, scale, ts_rank

__alpha_meta__ = {
    "id": "crypto_mined_market_regime_volatility",
    "nickname": "MarketRegimeVolatility",
    "theme": ["volatility"],
    "formula_latex": r"-\mathrm{scale}\left(\mathrm{rank}_{20}(\sigma_i)\cdot\sigma_{BTC,20}\right)",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 30,
    "notes": "Negative volatility factor scaled by BTC realized vol (regime proxy). When BTC vol spikes (event shock), tilt long book toward low-vol coins (defensive).",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"].astype(float)

    if "BTC-USDT" in close.columns:
        btc = close["BTC-USDT"]
    else:
        btc = close.mean(axis=1)

    rets = close.pct_change()
    btc_vol = btc.pct_change().rolling(20).std().fillna(0.0)
    per_coin_vol = rets.rolling(20).std().rank(axis=1, pct=True).fillna(0.5)
    # 波动率高 → 乘数大 → 低波动币 (rank 低) 得分高
    score = -per_coin_vol.mul(btc_vol, axis=0)
    return decay_linear(scale(score), 3)
