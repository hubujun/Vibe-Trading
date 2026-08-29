"""宏观事件与市场制度 (regime) — 三层事件结合方案.

第 1 层: 事件日历 + 级别 → 事件日自动降杠杆 (实时风控)
    - A 级 (监管立法/央行会议/黑天鹅): exposure × 0.5
    - B 级 (ETF 批复/国债回购/大厂动作): exposure × 0.8
    - C 级 (行业会议/普通公告): 不调整
第 2 层: regime 判定 → 全局缩仓 + 多空不对称
    - risk_on  (BTC 20d 动量强 + 波动率温和): 满仓对称
    - risk_off (BTC 20d 动量弱): 全局缩仓 + 空头腿打折 (暴跌时空头腿被轧)
    - neutral: 对称满仓
第 3 层: 市场状态特征 (可回测的 regime 代理因子, 供 variant_generator 因子池)
    - BTC 20d 动量 / 波动率 / 市场宽度 → 事件冲击的代理 (回测窗口内可计算)

事件数据: 内置 DEFAULT_EVENTS (手动维护, 跟随代码走) + 用户文件
~/.vibe-trading/macro_events.json 合并; CoinMarketCal API 拉取预留
(配置 CMCal_API_TOKEN 后启用 fetch_events()).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

_EVENTS_PATH = Path.home() / ".vibe-trading" / "macro_events.json"

# 事件级别: A=重大 (监管立法/央行/黑天鹅) B=中等 (ETF/回购/大厂) C=轻微
LEVEL_MULTIPLIER = {"A": 0.5, "B": 0.8, "C": 1.0}
LEVEL_LABEL = {"A": "重大", "B": "中等", "C": "轻微"}

# 内置事件日历 (2026-08 已知事件 + 结构示例). 真实事件请维护到用户文件.
DEFAULT_EVENTS: list[dict[str, Any]] = [
    {
        "date": "2026-08-19",
        "title": "特朗普召集 Coinbase/Kraken/Robinhood/Ripple 高层 + SEC/CFTC 主席会议, 呼吁国会通过 CLARITY Act",
        "level": "A",
        "tags": ["regulatory", "policy"],
    },
    {
        "date": "2026-08-19",
        "title": "美国财政部宣布扩大长期国债回购",
        "level": "B",
        "tags": ["macro", "treasury"],
    },
]

_KNOWN_EVENT_HASHES: set[str] = set()


def _today() -> date:
    return date.today()


def load_events() -> list[dict[str, Any]]:
    """内置事件 + 用户文件合并 (用户文件覆盖同日同标题)."""
    events = [dict(e) for e in DEFAULT_EVENTS]
    try:
        user = json.loads(_EVENTS_PATH.read_text(encoding="utf-8"))
        if isinstance(user, list):
            events.extend(user)
        elif isinstance(user, dict) and isinstance(user.get("events"), list):
            events.extend(user["events"])
    except (OSError, ValueError, TypeError):
        pass
    return events


def events_on(d: date | None = None) -> list[dict[str, Any]]:
    """返回指定日期 (默认今天) 的事件."""
    d = d or _today()
    out = []
    for e in load_events():
        try:
            if date.fromisoformat(str(e["date"])) == d:
                out.append(e)
        except (ValueError, KeyError):
            continue
    return out


def event_leverage_multiplier(d: date | None = None) -> float:
    """第 1 层: 事件日降杠杆. 取当日最高级别事件对应的乘数."""
    mult = 1.0
    for e in events_on(d):
        m = LEVEL_MULTIPLIER.get(str(e.get("level", "C")).upper(), 1.0)
        mult = min(mult, m)
    return mult


def get_regime(
    close_df: pd.DataFrame | None = None,
    d: date | None = None,
    mom_window: int = 20,
    risk_on_mom: float = 0.04,
    risk_off_mom: float = -0.04,
) -> dict[str, Any]:
    """第 2 层: 市场制度判定 (BTC 动量 + 波动率).

    参数:
        close_df: 全市场收盘价 DataFrame (含 BTC-USDT 列). None 时返回 neutral.
    返回:
        {regime, long_factor, short_factor, reason, btc_mom, btc_vol}
    """
    if close_df is None or "BTC-USDT" not in close_df.columns:
        return {"regime": "neutral", "long_factor": 1.0, "short_factor": 1.0,
                "reason": "无行情数据", "btc_mom": None, "btc_vol": None}
    btc = close_df["BTC-USDT"].astype(float).dropna()
    if len(btc) < mom_window + 2:
        return {"regime": "neutral", "long_factor": 1.0, "short_factor": 1.0,
                "reason": "BTC 数据不足", "btc_mom": None, "btc_vol": None}
    mom = btc.iloc[-1] / btc.iloc[-1 - mom_window] - 1.0
    vol = btc.pct_change().tail(mom_window).std() * (252 ** 0.5)

    if mom >= risk_on_mom:
        regime, lf, sf, reason = "risk_on", 1.0, 1.0, f"BTC 20d 动量 +{mom:.1%}"
    elif mom <= risk_off_mom:
        # 空头腿打折: 下跌趋势中做空腿常被反弹轧, 缩空头暴露
        regime, lf, sf, reason = "risk_off", 0.7, 0.5, f"BTC 20d 动量 {mom:.1%} (空头腿打折防轧)"
    else:
        regime, lf, sf, reason = "neutral", 1.0, 1.0, f"BTC 20d 动量 {mom:+.1%} (区间震荡)"
    return {"regime": regime, "long_factor": lf, "short_factor": sf,
            "reason": reason, "btc_mom": round(float(mom), 4),
            "btc_vol": round(float(vol), 4)}


# ---------------------------------------------------------------------------
# 第 3 层: 市场状态特征 (可回测的 regime 代理因子)
# ---------------------------------------------------------------------------
def market_state_features(close_df: pd.DataFrame | None = None, mom_window: int = 20) -> dict[str, float]:
    """给事件/regime 因子用的市场状态特征 (回测窗口内可计算).

    返回: {btc_mom_20d, btc_vol_20d, breadth_20d}
      - btc_mom_20d: BTC 20d 动量 (事件冲击的方向代理)
      - btc_vol_20d: BTC 年化波动率 (事件冲击的剧烈程度代理)
      - breadth_20d: 市场宽度 = 20d 涨幅为正的币占比 (risk-on/off 的广度)
    """
    if close_df is None:
        return {"btc_mom_20d": 0.0, "btc_vol_20d": 0.0, "breadth_20d": 0.5}
    out: dict[str, float] = {}
    if "BTC-USDT" in close_df.columns:
        btc = close_df["BTC-USDT"].astype(float).dropna()
        if len(btc) > mom_window:
            out["btc_mom_20d"] = round(float(btc.iloc[-1] / btc.iloc[-1 - mom_window] - 1.0), 4)
            out["btc_vol_20d"] = round(float(btc.pct_change().tail(mom_window).std() * (252 ** 0.5)), 4)
    else:
        out["btc_mom_20d"] = 0.0
        out["btc_vol_20d"] = 0.0
    rets = close_df.astype(float).pct_change().tail(mom_window)
    if len(rets):
        up_ratio = float((rets.iloc[-1] > 0).mean()) if len(rets) else 0.5
        out["breadth_20d"] = round(up_ratio, 4)
    else:
        out["breadth_20d"] = 0.5
    return out


# ---------------------------------------------------------------------------
# CoinMarketCal API 预留 (配置 CMCal_API_TOKEN 后可用)
# ---------------------------------------------------------------------------
def fetch_events_from_api(days_ahead: int = 7) -> list[dict[str, Any]]:
    """从 CoinMarketCal 拉取未来事件 (需 token, 未配置返回空)."""
    token = os.environ.get("CMCal_API_TOKEN", "")
    if not token:
        return []
    try:
        import urllib.request

        start = date.today()
        end = start + timedelta(days=days_ahead)
        url = (
            "https://api.coinmarketcal.com/v1/events"
            f"?dateRangeStart={start}&dateRangeEnd={end}"
            f"&access_token={token}"
        )
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = json.loads(resp.read())
        out = []
        for e in raw:
            out.append({
                "date": e.get("date_event") or e.get("date_event_end"),
                "title": e.get("title", ""),
                "level": "C",  # API 无级别 → 默认 C, 重大事件手动标 A
                "tags": ["external"],
                "source": "coinmarketcal",
            })
        return [e for e in out if e.get("date")]
    except Exception as exc:  # noqa: BLE001
        print(f"  [macro_events] CoinMarketCal 拉取失败: {exc}")
        return []


if __name__ == "__main__":
    d = date.today()
    evs = events_on(d)
    print(f"今日 ({d}) 事件: {evs if evs else '无'}")
    print(f"事件降杠杆: x{event_leverage_multiplier(d)}")
    print(f"市场状态: {market_state_features()}")
