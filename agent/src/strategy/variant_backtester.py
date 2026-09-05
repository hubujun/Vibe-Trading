"""变体自动回测引擎 — Loop Engineering 第四圈: 自动进化/自举.

对假设注册表里的 exploring 变体 (signal_definition 为 ``combo_variant:``)
自动跑回测并晋升:

1. 解析 signal_definition → factors / weights / top_n / bot_n
2. 拉一次 OKX 日线 panel (close + volume), 所有变体复用
3. 因子 compute → 逐行横截面 z-score → 等权合成 → 多 top_n 空 bot_n
   → 日频调仓计成本 → 年化/夏普/最大回撤/累计
4. 晋升规则 (保守, 与基策略 COMBO2 对比):
   - 年化 > 基年化 且 夏普 > 基夏普 且 回撤 < 基回撤 × 1.5 → testing (可进模拟)
   - 否则保持 exploring, 指标记录在案 (不淘汰, 供人工判断)
5. 结果缓存 ``~/.vibe-trading/runs/paper_combo/variant_backtests.json``
   (signal_definition → metrics), 幂等: 已有缓存不重跑

触发: 独立 cron (每日), 或命令行手动. 不进 workbench GET 热路径.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.hypotheses.registry import HypothesisRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "parse_signal_definition",
    "load_factor_module",
    "backtest_variant",
    "run_variant_backtests",
    "load_backtest_cache",
]

#: 基策略回测对比基准 (COMBO2, 与 combo_backtest.py 一致).
BASE_METRICS = {"annual": 12.77, "sharpe": 0.79, "max_dd": -10.62}

#: 回撤宽松倍数 (晋升条件: 变体回撤 > 基回撤 × 该倍数 → 否决)
DD_TOLERANCE = 1.5

#: 缓存路径
HOME = Path.home()
CACHE_PATH = HOME / ".vibe-trading" / "runs" / "paper_combo" / "variant_backtests.json"
HYPOTHESES_PATH = HOME / ".vibe-trading" / "hypotheses.json"

#: 币种 universe (与 daily_signal/autopilot 一致) — 17 个主流+次主流 USDT 对
#: 覆盖公链/平台币/DeFi/预言机/meme 多板块, 均 OKX 永续 + 历史数据够 800 天回测
SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
    "DOGE-USDT", "OKB-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT",
    "LTC-USDT", "DOT-USDT", "UNI-USDT", "APT-USDT", "ARB-USDT",
    "TRUMP-USDT", "LAB-USDT",
]

#: 无现货、只有永续的币 → 蜡烛用永续 instId (LAB 2025-11 上市, 无现货交易对)
PERP_ONLY: dict[str, str] = {
    "LAB-USDT": "LAB-USDT-SWAP",
}

#: 币种板块映射 (机构实践: 组合构建时控制板块暴露) — 单源定义, daily_signal 复用
SECTOR: dict[str, str] = {
    "BTC-USDT": "chain", "ETH-USDT": "chain", "SOL-USDT": "chain",
    "XRP-USDT": "chain", "ADA-USDT": "chain", "AVAX-USDT": "chain",
    "LTC-USDT": "chain", "DOT-USDT": "chain", "APT-USDT": "chain",
    "ARB-USDT": "chain",
    "BNB-USDT": "cex", "OKB-USDT": "cex",
    "UNI-USDT": "defi", "LINK-USDT": "defi",
    "DOGE-USDT": "meme", "TRUMP-USDT": "meme", "LAB-USDT": "meme",
}
#: 同板块最多入选数 (防 meme/单板块权重过度集中)
SECTOR_CAP = 2

#: 组合波动率目标 (年化) — 回测/实盘一致的连续风控
VOL_TARGET = 0.25

#: 疯牛保险 — 普涨环境自动降仓 (防 2021 式普涨轧空, 回测/实盘一致)
#: 信号: 上涨广度 >50% 币 20d 动量 >15% 且 BTC 20d 动量 >8% → 杠杆乘数 0.4
CRAZY_BULL_MULT = 0.4
CRAZY_BREADTH_TH = 0.5
CRAZY_MOM_TH = 0.15
CRAZY_BTC_TH = 0.08


def _crazy_bull_mult(close: pd.DataFrame) -> pd.Series:
    """疯牛保险乘数 — 普涨环境全局降仓 (0.4 或 1.0, 与 daily_signal 一致).

    普涨疯牛 (上涨广度 >50% 且 BTC 20d 动量 >8%) 时全局缩到 CRAZY_BULL_MULT,
    防多空对冲被普涨轧空 (2021 疯牛回测 -50% 回撤的根源; 2700 天多周期回测:
    全期年化 +9%→+12.3%, 极值回撤 -65.3%→-56.9%).
    返回逐日乘数 Series, 调用方 shift(1) 防前视.
    """
    mom20 = close.pct_change(20)
    if "BTC-USDT" in close.columns:
        btc_mom = close["BTC-USDT"].pct_change(20)
    else:
        btc_mom = pd.Series(0.0, index=close.index)
    avail = mom20.notna().sum(axis=1)
    breadth = (mom20 > CRAZY_MOM_TH).sum(axis=1) / avail.replace(0, np.nan)
    sig = (breadth > CRAZY_BREADTH_TH) & (btc_mom > CRAZY_BTC_TH)
    return pd.Series(np.where(sig, CRAZY_BULL_MULT, 1.0), index=close.index)


def _sector_cap(ranking: list[str], n: int, cap: int = SECTOR_CAP) -> list[str]:
    """板块权重上限 — 从强到弱选 n 个, 同板块最多 cap 个 (与 daily_signal 一致)."""
    picked: list[str] = []
    counts: dict[str, int] = {}
    for sym in ranking:
        sec = SECTOR.get(sym, "other")
        if counts.get(sec, 0) >= cap:
            continue
        picked.append(sym)
        counts[sec] = counts.get(sec, 0) + 1
        if len(picked) >= n:
            break
    return picked
DAYS = 800
COST = 0.001
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

#: academic 因子 → 模块名映射 (全部 13 个, 均只需 close ± volume)
ACADEMIC_MODULES: dict[str, str] = {
    "BAB": "bab",
    "RMW": "rmw",
    "high52w": "high52w",
    "carhart_mom": "carhart_mom",
    "cma": "cma",
    "corr_rewire": "corr_rewire",
    "hml": "hml",
    "illiq": "illiq",
    "mkt_rf": "mkt_rf",
    "retskew": "retskew",
    "smb": "smb",
    "strev": "strev",
}


# ============================================================================
# signal_definition 解析
# ============================================================================


def parse_signal_definition(sig_def: str) -> dict[str, Any] | None:
    """解析 ``combo_variant: factors=[...] weights={...} top_n=3 bot_n=3``.

    可选超跌补涨过滤 (空头腿, 防慢牛轧空):
      short_dd_th=-0.60 short_mom_th=0.10 — 排除 距52周高点回撤<short_dd_th 且
      20d 动量>short_mom_th 的币 (超跌且正在补涨 → 不空).
    返回 {factors, weights, top_n, bot_n, short_dd_th, short_mom_th};
    无法解析返回 None.
    """
    if not sig_def or not sig_def.startswith("combo_variant:"):
        return None
    body = sig_def[len("combo_variant:"):].strip()

    weights: dict[str, float] = {}
    m_w = re.search(r"weights=\{(.*?)\}", body)
    if m_w:
        for pair in m_w.group(1).split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                weights[k.strip()] = float(v)

    factors: list[str] = []
    m_f = re.search(r"factors=\[(.*?)\]", body)
    if m_f and m_f.group(1).strip():
        factors = [x.strip() for x in m_f.group(1).split(",")]
    if not factors:
        factors = list(weights.keys())

    top_n = 3
    m_t = re.search(r"top_n=(\d+)", body)
    if m_t:
        top_n = int(m_t.group(1))
    bot_n = 3
    m_b = re.search(r"bot_n=(\d+)", body)
    if m_b:
        bot_n = int(m_b.group(1))

    # 超跌补涨过滤参数 (可选; 无 → None = 不过滤, 兼容旧定义)
    short_dd_th = None
    m_dd = re.search(r"short_dd_th=([-0-9.]+)", body)
    if m_dd:
        short_dd_th = float(m_dd.group(1))
    short_mom_th = None
    m_mom = re.search(r"short_mom_th=([-0-9.]+)", body)
    if m_mom:
        short_mom_th = float(m_mom.group(1))

    if not factors or not weights:
        return None
    return {
        "factors": factors, "weights": weights, "top_n": top_n, "bot_n": bot_n,
        "short_dd_th": short_dd_th, "short_mom_th": short_mom_th,
    }


# ============================================================================
# 因子模块加载
# ============================================================================


def load_factor_module(factor_id: str) -> Any | None:
    """按 id 加载因子 compute 模块 (academic 或 crypto_mined zoo)."""
    try:
        if factor_id in ACADEMIC_MODULES:
            mod = __import__(
                f"src.factors.zoo.academic.{ACADEMIC_MODULES[factor_id]}",
                fromlist=["compute"],
            )
        else:
            mod = __import__(
                f"src.factors.zoo.crypto_mined.{factor_id}", fromlist=["compute"],
            )
        return mod if hasattr(mod, "compute") else None
    except Exception:  # noqa: BLE001
        logger.warning("variant backtest: cannot load factor %s", factor_id)
        return None


# ============================================================================
# 数据
# ============================================================================


def fetch_okx_daily(symbol: str) -> pd.DataFrame | None:
    """OKX 日线 close + volume (走 ClashX 代理, 分页)."""
    import requests

    url = "https://www.okx.com/api/v5/market/history-candles"
    all_rows: list[list] = []
    after = None
    for page in range(math.ceil(DAYS / 100)):
        params = {"instId": PERP_ONLY.get(symbol, symbol), "bar": "1D", "limit": "100"}
        if after:
            params["after"] = str(after)
        for attempt in range(4):
            try:
                r = requests.get(url, params=params, timeout=15, proxies=PROXY)
                data = r.json().get("data", [])
                if not data:
                    break
                all_rows.extend(data)
                after = min(int(x[0]) for x in data)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    logger.warning("fetch fail %s: %s", symbol, str(e)[:50])
                    return None
                time.sleep(2)
    if not all_rows:
        return None
    rows = sorted(all_rows, key=lambda x: int(x[0]))[-DAYS:]
    df = pd.DataFrame(rows, columns=[
        "ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm",
    ])
    # OKX candle ts 为北京时间 00:00 (UTC+8), 归一到北京日期 (与 daily_signal 一致)
    df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms") + pd.Timedelta(hours=8)
    df["ts"] = df["ts"].dt.normalize()
    df = df.set_index("ts")
    df["close"] = df["close"].astype(float)
    df["volume"] = df["vol"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["open"] = df["open"].astype(float)
    return df[["close", "volume", "high", "low", "open"]]


def fetch_panel() -> dict[str, pd.DataFrame] | None:
    """拉 17 币 panel (close + volume + high + low + open), 对齐后返回; 数据不足返回 None.

    新上市币 (TRUMP/LAB 等 <800 天) 有多少数据测多少天 —
    对齐时 dropna 自动用公共区间 (最早上市的币决定窗口长度).
    仅剔除数据过少 (<60 天) 无统计意义的币.
    """
    frames: dict[str, pd.DataFrame] = {}
    for s in SYMBOLS:
        df = fetch_okx_daily(s)
        if df is None or len(df) < 60:
            print(f"  [fetch_panel] {s} 数据过少, 跳过")
            time.sleep(0.3)
            continue
        frames[s] = df
        time.sleep(0.3)
    if len(frames) < 4:
        return None
    close = pd.DataFrame({s: f["close"] for s, f in frames.items()})
    volume = pd.DataFrame({s: f["volume"] for s, f in frames.items()})
    high = pd.DataFrame({s: f["high"] for s, f in frames.items()})
    low = pd.DataFrame({s: f["low"] for s, f in frames.items()})
    open_ = pd.DataFrame({s: f["open"] for s, f in frames.items()})
    # 各币保留自己的可用长度: 新币前段保持 NaN (有多少测多少, 不拉短老币窗口)
    close = close.dropna(axis=1, how="all").ffill()
    volume = volume.reindex(close.index).ffill()
    high = high.reindex(close.index).ffill()
    low = low.reindex(close.index).ffill()
    open_ = open_.reindex(close.index).ffill()
    if close.shape[0] < 300 or close.shape[1] < 4:
        return None
    return {"close": close, "volume": volume, "high": high, "low": low, "open": open_}


# ============================================================================
# 回测
# ============================================================================


def _row_zscore(df: pd.DataFrame) -> pd.DataFrame:
    """逐行横截面 z-score (因子标准化, 合成前统一量纲)."""
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([np.inf, -np.inf], np.nan)


def backtest_variant(
    panel: dict[str, pd.DataFrame],
    factors: list[str],
    weights: dict[str, float],
    top_n: int,
    bot_n: int,
    dynamic_n: bool = True,
    short_dd_th: float | None = None,
    short_mom_th: float | None = None,
) -> dict[str, Any]:
    """对单个变体跑回测, 返回指标 dict.

    dynamic_n=True: 温和版动态多空比 — 按日 BTC 20d 动量调整:
      牛市 (≥+4%): 3 多 + 2 空 (减空头腿); 熊市 (≤-4%): 2 多 + 3 空 (减多头腿);
      震荡: 3 + 3 对称. 用行级 mask 实现 (每日期限不同).
    short_dd_th/short_mom_th: 超跌补涨过滤 — 空头候选排除 距52周高点回撤
      <short_dd_th 且 20d 动量 >short_mom_th 的币 (慢牛补涨轧空防护).
    """
    close = panel["close"]
    rets = close.pct_change()

    # 因子得分合成 — 任何因子加载失败(文件缺失)即拒绝回测, 不许静默摊权:
    # 摊权让三因子变体退化成基座组合, 产出与基策略逐位相同的假指标
    # (2026-09-05: 4 条 monitoring 僵尸引用 8-23 前丢失的因子文件, 摊权后
    #  与基策略同 quad → E2E 30.13% 事故回归误报; 缺失=永久, 必须 fail loud)
    raw_mods = {fid: load_factor_module(fid) for fid in factors}
    missing = [fid for fid, m in raw_mods.items() if m is None]
    if missing:
        logger.warning(
            "variant backtest: refuse to backtest %s — factor files missing: %s",
            factors, missing,
        )
        return {"error": f"missing factor files: {missing}"}
    mods: dict[str, Any] = {fid: m for fid, m in raw_mods.items() if m is not None}
    score_sum = None
    weight_sum = 0.0
    compute_failed: list[str] = []
    for fid, mod in mods.items():
        try:
            f = mod.compute(panel).reindex(close.index)
            # 学术因子 compute 已返回横截面 z-score; zoo 因子 raw → 行 z-score 统一
            if fid not in ACADEMIC_MODULES:
                f = _row_zscore(f)
        except Exception:  # noqa: BLE001
            compute_failed.append(fid)
            continue
        w = float(weights.get(fid, 0.0))
        if w <= 0:
            continue
        score_sum = f * w if score_sum is None else score_sum + f * w
        weight_sum += w
    if compute_failed:
        logger.warning(
            "variant backtest: refuse to backtest %s — factor compute failed: %s",
            factors, compute_failed,
        )
        return {"error": f"factor compute failed: {compute_failed}"}
    if score_sum is None or weight_sum <= 0:
        return {"error": "no usable factors"}
    combo = score_sum / weight_sum

    # 多 top_n 空 bot_n — 逐日选币, 与 daily_signal 完全一致:
    #   动态多空比 (牛市减空/熊市减多) + 板块上限 (同板块最多 SECTOR_CAP 个)
    r = combo.rank(axis=1, method="first")
    n = close.shape[1]
    if "BTC-USDT" in close.columns:
        btc_mom = close["BTC-USDT"].pct_change(20)
    else:
        btc_mom = pd.Series(0.0, index=close.index)
    # 超跌补涨过滤特征 (空头腿): 距52周高点回撤 + 20d 动量
    _dd52 = close / close.rolling(252).max() - 1
    _mom20 = close.pct_change(20)
    w = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for dt in close.index:
        row = combo.loc[dt].dropna().sort_values(ascending=False)
        if len(row) < 4:
            continue
        mom = btc_mom.get(dt, 0.0)
        if pd.isna(mom):
            tn, bn = top_n, bot_n
        elif mom >= 0.04:
            tn, bn = top_n, max(1, bot_n - 1)          # 牛市: 减 1 空头
        elif mom <= -0.04:
            tn, bn = max(1, top_n - 1), bot_n          # 熊市: 减 1 多头
        else:
            tn, bn = top_n, bot_n
        longs = _sector_cap(row.index.tolist(), tn)
        short_cands = row.index.tolist()[::-1]
        if short_dd_th is not None and short_mom_th is not None:
            # 超跌补涨过滤: 排除 距高点回撤<阈值 且 20d动量>阈值 的币 (防慢牛轧空)
            short_cands = [
                c for c in short_cands
                if not (_dd52.loc[dt, c] < short_dd_th and _mom20.loc[dt, c] > short_mom_th)
            ]
        if not short_cands:
            continue
        bn_eff = min(bn, len(short_cands))
        shorts = _sector_cap(short_cands, bn_eff)
        w.loc[dt, longs] = 1.0
        w.loc[dt, shorts] = -1.0
    w = w.div(w.abs().sum(axis=1), axis=0)

    daily = (w.shift(1) * rets).sum(axis=1)
    turnover = w.diff().abs().sum(axis=1) / 2
    # 波动率目标 (与 daily_signal 一致): 组合滚动波动率 >25% 年化自动缩仓
    vol = daily.rolling(20).std() * math.sqrt(252)
    vol_mult = (VOL_TARGET / vol.replace(0, float("nan"))).clip(0.3, 1.5)
    vol_mult = vol_mult.shift(1).fillna(1.0)
    # 疯牛保险 (与 daily_signal 一致): 普涨环境全局降仓 (防轧空), shift(1) 防前视
    crazy_mult = _crazy_bull_mult(close).shift(1).fillna(1.0)
    net = daily * vol_mult * crazy_mult - turnover * COST
    nav = (1 + net.fillna(0)).cumprod()
    total = float(nav.iloc[-1] - 1)
    years = len(nav) / 365
    annual = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else -1.0
    sharpe = float(net.mean() / net.std() * math.sqrt(365)) if net.std() > 0 else 0.0
    peak = nav.cummax()
    max_dd = float(((nav / peak) - 1).min())
    return {
        "annual": round(annual * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_dd": round(max_dd * 100, 2),
        "cum": round(total * 100, 2),
        "turnover": round(float(turnover.mean()), 4),
        "days": int(len(nav)),
        "factors": factors,
    }


# ============================================================================
# 缓存
# ============================================================================


#: 回测缓存逻辑版本 — 回测逻辑变更时 +1, 缓存自动失效全量重算
#: (v2: 动态多空比 + 板块上限 + 波动率目标; v3: + 疯牛保险普涨降仓, 与 daily_signal 一致)
CACHE_LOGIC_VERSION = 5


def load_backtest_cache() -> dict[str, dict[str, Any]]:
    """读变体回测缓存 (fail-open). 逻辑版本不匹配 → 返回空 (全量重算)."""
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        if raw.get("meta", {}).get("logic_version") != CACHE_LOGIC_VERSION:
            logger.warning("variant backtest: cache logic v%d != v%d, recompute all",
                           raw.get("meta", {}).get("logic_version"), CACHE_LOGIC_VERSION)
            return {}
        return {k: v for k, v in raw.items() if k != "meta"}
    except (OSError, ValueError, TypeError):
        return {}


def save_backtest_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": {"logic_version": CACHE_LOGIC_VERSION}, **cache}
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE_PATH)


# ============================================================================
# 晋升规则
# ============================================================================


def _promote_status(metrics: dict[str, Any], base: dict[str, Any] | None = None) -> str:
    """晋升判定: 年化&夏普双超 + 回撤可控 → testing; 否则保持 exploring.

    base: 当前 universe 下的基策略回测基准 (默认硬编码 BASE_METRICS).
    """
    base = base or BASE_METRICS
    annual = metrics.get("annual")
    sharpe = metrics.get("sharpe")
    max_dd = metrics.get("max_dd")
    if annual is None or sharpe is None or max_dd is None:
        return "exploring"
    if (
        annual > base["annual"]
        and sharpe > base["sharpe"]
        and max_dd > base["max_dd"] * DD_TOLERANCE
    ):
        return "testing"
    return "exploring"


#: 基策略组合定义 (用于动态重算当前 universe 下的晋升基准)
_BASE_SIGNAL = {
    "factors": ["BAB", "high52w"],
    "weights": {"BAB": 0.5, "high52w": 0.5},
    "top_n": 3,
    "bot_n": 3,
}
_BASE_CACHE_KEY = "_BASE_"


def _load_base_metrics(cache: dict[str, dict[str, Any]], panel: dict[str, pd.DataFrame]) -> tuple[dict[str, Any], bool]:
    """当前 universe 下基策略的回测基准 (缓存 _BASE_, universe 变化自动重算).

    Returns:
        (base_metrics, changed) — changed=True 表示基准本轮重算,
        调用方需对已缓存变体重判晋升状态.
    """
    base = cache.get(_BASE_CACHE_KEY)
    # 用 panel 实际币数 (新上市币数据不足被跳过时 universe_size 变小)
    actual_universe = len(panel.get("close", pd.DataFrame()).columns)
    if base and base.get("universe_size") == actual_universe:
        return base, False
    m = backtest_variant(panel, _BASE_SIGNAL["factors"], _BASE_SIGNAL["weights"], 3, 3)
    if "error" in m:
        logger.warning("variant backtest: base metrics failed (%s), fallback hardcoded", m["error"])
        return BASE_METRICS, False
    base = {
        "annual": m["annual"],
        "sharpe": m["sharpe"],
        "max_dd": m["max_dd"],
        "cum": m.get("cum"),  # 累计收益 — 2026-08-30 补: 前端研究卡基策略 cum
        "universe_size": len(SYMBOLS),
        "backtested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    cache[_BASE_CACHE_KEY] = base
    return base, True


# ============================================================================
# 主入口
# ============================================================================


# ============================================================================
# 自动播种: 晋升变体 → 并行策略 (与 workbench_routes 播种端点同结构)
# ============================================================================


#: 策略注册表路径 (workbench_routes 同源; 测试可 monkeypatch 注入)
_STRATEGIES_PATH = Path.home() / ".vibe-trading" / "workbench" / "strategies.json"


def _auto_seed_strategy(signal_definition: str, name: str) -> str | None:
    """晋升的变体自动播种为新策略并直接进入模拟盘 (phase=paper).

    - strategies.json 按 signal_definition 去重 (已播种则跳过)
    - 独立 run_dir + 初始 state.json (nav=1.0), 次日 07:00 cron 自动跑信号
    """
    import json as _json

    p = _STRATEGIES_PATH
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # 首次播种: 文件不存在是合法场景
        data = {"strategies": []}
    except (OSError, ValueError, TypeError):
        # 文件存在但读取失败/损坏 — 绝不能静默覆盖 (会把 strategies.json 清空)
        logger.error("auto-seed 中止: strategies.json 读取失败 (%s)", p)
        return None
    sids = [str(s.get("signal_definition", "")) for s in data.get("strategies", [])]
    if signal_definition in sids:
        return None
    parsed = parse_signal_definition(signal_definition)
    if parsed is None:
        return None
    sid = "combo_" + hashlib.md5(signal_definition.encode()).hexdigest()[:8]
    run_dir = Path.home() / ".vibe-trading" / "runs" / f"paper_{sid}"
    run_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (run_dir / "state.json").write_text(
        _json.dumps(
            {
                "strategy_id": sid,
                "nav": 1.0,
                "trades": [],
                "started_at": now,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    data.setdefault("strategies", []).append(
        {
            "strategy_id": sid,
            "name": str(name).strip(),
            "phase": "paper",
            "factors": parsed["factors"],
            "weights": parsed["weights"],
            "top_n": parsed["top_n"],
            "bot_n": parsed["bot_n"],
            "universe_size": len(SYMBOLS),
            "rebalance": "日频 · 每日 07:00",
            "signal_definition": signal_definition,
            "run_dir": str(run_dir),
            "phase_history": [
                {
                    "phase": "paper",
                    "at": now,
                    "action": "seeded",
                    "note": "晋升变体自动播种进模拟盘",
                }
            ],
            "created_at": now,
            "updated_at": None,
        }
    )
    p.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return sid


def _duplicate_factor_check(panel: dict[str, pd.DataFrame], factors: list[str],
                            pool: list[str] | None = None,
                            corr_threshold: float = 0.9) -> str | None:
    """机构实践: 因子相关性去重 — 新增因子与基座/池内因子 |截面相关|>阈值 → 冗余.

    pool: 因子池 (已存在变体用过的因子) — 全池去重: 新增因子与池内任何因子
    高相关都判冗余, 不只查基座 (2026-08-30 共线性诊断: illiq×smb 0.95 等
    马甲因子就是只查基座漏进来的).

    返回冗余因子名 (无增量 alpha, 该变体直接否决); 无冗余返回 None.
    """
    base = [f for f in factors if f in ("BAB", "high52w")]
    new = [f for f in factors if f not in ("BAB", "high52w")]
    refs = base + [f for f in (pool or []) if f not in base]
    if not refs or not new:
        return None
    vals: dict[str, pd.DataFrame] = {}
    for fid in factors + refs:
        if fid in vals:
            continue
        mod = load_factor_module(fid)
        if mod is None:
            return None
        try:
            vals[fid] = mod.compute(panel).reindex(panel["close"].index).ffill()
        except Exception:  # noqa: BLE001
            return None
    for nf in new:
        for bf in refs:
            if nf == bf or bf not in vals or nf not in vals:
                continue
            c = float(vals[nf].corrwith(vals[bf], axis=1).mean())
            if abs(c) > corr_threshold:
                return nf
    return None


def _factor_split_ic_check(panel: dict[str, pd.DataFrame], factors: list[str],
                           min_days: int = 60) -> tuple[str | None, float | None, float | None]:
    """分时段稳定性闸门 (2026-08-30, 防时变因子/过拟合) — 挖掘因子的前/后半段 IC 符号必须一致.

    动机: 单次全窗口回测会把'前半段有效后半段失效'的时变因子当成好因子,
    这类因子实盘大概率是噪声. 对变体的每个新增挖掘因子 (非学术基座):
    - 前 50% / 后 50% 历史分别算截面 rank IC 均值
    - 符号相反且 |差| > 0.01 (排除纯噪声 ±0.001) → 判不稳定, 变体否决
    - 模块加载失败 / compute 异常 → 判不可用 (与僵尸因子同源问题, 直接拦下)

    返回 (fail_factor, ic_front, ic_back); 通过返回 (None, ic_front, ic_back).
    """
    close = panel["close"]
    rets = close.pct_change().shift(-1)
    n = len(close)
    if n < min_days * 2 + 10:
        return None, None, None
    split = n // 2
    for fid in factors:
        if fid in ACADEMIC_MODULES:
            continue
        mod = load_factor_module(fid)
        if mod is None:
            return fid, None, None
        try:
            f = mod.compute(panel).reindex(close.index)
            f = _row_zscore(f)
        except Exception:  # noqa: BLE001
            return fid, None, None
        ics: list[float | None] = []
        for lo, hi in ((0, split), (split, n)):
            fr = f.iloc[lo:hi].rank(axis=1)
            rr = rets.iloc[lo:hi].rank(axis=1)
            ic = fr.corrwith(rr, axis=1).dropna()
            ic = ic[ic.abs() < 1]
            ics.append(float(ic.mean()) if len(ic) >= min_days else None)
        front, back = ics
        if front is None or back is None:
            continue
        if (front >= 0) != (back >= 0) and abs(front - back) > 0.01:
            return fid, round(front, 4), round(back, 4)
    return None, None, None


STRESS_SCENARIOS = ("crash", "liquidity", "flash", "macro", "all")


def _find_stress_windows(scenario: str,
                         panel: dict[str, pd.DataFrame]) -> list[tuple[pd.Timestamp, pd.DataFrame]]:
    """按场景发现压力窗口 (窗口 = 事件日 ±2/+3 天, 复用原有评估逻辑).

    crash      — BTC 单日跌 >5% (原有)
    liquidity  — BTC 跌 >3% 且全市场成交量中位数 < 前20日均量 60% (流动性危机)
    flash      — 任一币单日跌 >30% (单币闪崩)
    macro      — macro_events.json 中 A 级事件日期 (宏观冲击)
    all        — 全部合并
    """
    close = panel["close"]
    btc = close["BTC-USDT"] if "BTC-USDT" in close.columns else close.mean(axis=1)
    daily = btc.pct_change()
    windows: list[tuple[pd.Timestamp, pd.DataFrame]] = []

    def _add(d: pd.Timestamp) -> None:
        seg = close.loc[d - pd.Timedelta(days=2): d + pd.Timedelta(days=3)]
        if len(seg) >= 3:
            windows.append((d, seg))

    if scenario in ("crash", "all"):
        for d, v in daily.items():
            if v < -0.05:
                _add(d)

    if scenario in ("liquidity", "all"):
        vol = panel.get("volume")
        if vol is not None:
            vol_med = vol.median(axis=1)
            vol_ma = vol_med.rolling(20).mean()
            for d, v in daily.items():
                try:
                    if v < -0.03 and vol_med.get(d, float("inf")) < vol_ma.get(d, 1.0) * 0.6:
                        _add(d)
                except Exception:  # noqa: BLE001
                    continue

    if scenario in ("flash", "all"):
        rets = close.pct_change()
        for c in close.columns:
            for d, v in rets[c].items():
                if v < -0.30:
                    _add(d)

    if scenario in ("macro", "all"):
        try:
            import json as _json
            ev = _json.loads(
                (Path.home() / ".vibe-trading" / "macro_events.json").read_text(encoding="utf-8"))
            for e in ev.get("events", []):
                if str(e.get("level", "")).upper() == "A":
                    _add(pd.Timestamp(e["date"]))
        except Exception:  # noqa: BLE001
            pass

    seen: set[pd.Timestamp] = set()
    out: list[tuple[pd.Timestamp, pd.DataFrame]] = []
    for d, seg in windows:
        if d not in seen:
            seen.add(d)
            out.append((d, seg))
    return out


def run_stress_test(cache_path: Path = CACHE_PATH,
                    panel: dict[str, pd.DataFrame] | None = None,
                    scenario: str = "crash") -> dict[str, Any]:
    """机构实践: 压力测试 — 多场景危机窗口内评估已晋升变体的抗跌性.

    场景: crash(原有 BTC 崩盘) / liquidity(流动性危机) / flash(单币闪崩) /
          macro(宏观事件) / all.
    对 testing/validated/monitoring 变体: 用窗口起点因子得分选多空腿,
    计算窗口内组合收益 (等权, 含成本). 最差窗口收益 < -15% → 压力不通过 (rejected).
    """
    if scenario not in STRESS_SCENARIOS:
        return {"error": f"未知场景 {scenario}, 可选: {STRESS_SCENARIOS}"}
    if panel is None:
        panel = fetch_panel()
    if panel is None:
        return {"error": "panel fetch failed", "windows": [], "results": []}
    close = panel["close"]
    btc = close["BTC-USDT"] if "BTC-USDT" in close.columns else close.mean(axis=1)
    # 超跌补涨过滤特征 (与 backtest_variant 一致)
    _dd52 = close / close.rolling(252).max() - 1
    _mom20 = close.pct_change(20)
    windows = _find_stress_windows(scenario, panel)
    if not windows:
        return {"windows": [], "results": [], "note": f"数据范围内无 {scenario} 场景窗口"}

    registry = HypothesisRegistry(HYPOTHESES_PATH)
    cache = load_backtest_cache() if cache_path == CACHE_PATH else _read_cache(cache_path)
    results: list[dict[str, Any]] = []
    rejected_ids: list[str] = []
    for h in registry.list():
        sd = str(h.signal_definition)
        if not sd.startswith("combo_variant:") or sd not in cache:
            continue
        if str(h.status) not in ("testing", "validated", "monitoring"):
            continue
        parsed = parse_signal_definition(sd)
        if parsed is None:
            continue
        worst = 0.0
        worst_day = None
        for d, seg in windows:
            if len(seg) < 3:
                continue
            pre = close.loc[:d - pd.Timedelta(days=1)]
            if len(pre) < 60:
                continue
            # 窗口起点打分: 用窗口前一天的因子截面
            try:
                factors = parsed["factors"]
                weights = parsed["weights"]
                score = None
                tw = 0.0
                for fid in factors:
                    mod = load_factor_module(fid)
                    if mod is None:
                        continue
                    f = mod.compute(panel).reindex(close.index)
                    if fid not in ACADEMIC_MODULES:
                        f = f.sub(f.mean(axis=1), axis=0).div(f.std(axis=1).replace(0, 1), axis=0)
                    w = weights.get(fid, 1.0 / len(factors))
                    score = f * w if score is None else score.add(f * w, fill_value=0)
                    tw += w
                if score is None:
                    continue
                row = score.loc[d - pd.Timedelta(days=1)].dropna().sort_values(ascending=False)
                # 与 backtest_variant/daily_signal 一致的选币: 动态多空比 + 板块上限
                mom = btc.pct_change(20).get(d - pd.Timedelta(days=1), 0.0)
                if pd.isna(mom):
                    tn, bn = parsed["top_n"], parsed["bot_n"]
                elif mom >= 0.04:
                    tn, bn = parsed["top_n"], max(1, parsed["bot_n"] - 1)
                elif mom <= -0.04:
                    tn, bn = max(1, parsed["top_n"] - 1), parsed["bot_n"]
                else:
                    tn, bn = parsed["top_n"], parsed["bot_n"]
                longs = _sector_cap(row.index.tolist(), tn)
                short_cands = row.index.tolist()[::-1]
                sd_th = parsed.get("short_dd_th")
                sm_th = parsed.get("short_mom_th")
                if sd_th is not None and sm_th is not None:
                    # 超跌补涨过滤 (与 backtest_variant 一致): 排除超跌且正在补涨的币
                    short_cands = [
                        c for c in short_cands
                        if not (_dd52.loc[d - pd.Timedelta(days=1), c] < sd_th
                                and _mom20.loc[d - pd.Timedelta(days=1), c] > sm_th)
                    ]
                if not short_cands:
                    continue
                shorts = _sector_cap(short_cands, min(bn, len(short_cands)))
                rets = seg.pct_change().iloc[1:]
                rl = rets[longs].mean(axis=1).sum()
                rs = -rets[shorts].mean(axis=1).sum()
                win_ret = (rl + rs) / 2 - COST
                if win_ret < worst:
                    worst = win_ret
                    worst_day = d
            except Exception:  # noqa: BLE001
                continue
        results.append({
            "hypothesis_id": h.hypothesis_id,
            "title": h.title,
            "status": h.status,
            "worst_window_ret": round(float(worst) * 100, 2),
            "worst_day": str(worst_day.date()) if worst_day else None,
            "stress_fail": bool(worst < -0.15),
        })
        if worst < -0.15:
            rejected_ids.append(h.hypothesis_id)
            try:
                registry.update(
                    h.hypothesis_id,
                    status="rejected",
                    invalidation_notes=(
                        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}: "
                        f"压力测试({scenario})不通过 — {scenario} 窗口 ({worst_day.date()}) 组合收益 {worst*100:.1f}%"
                    ),
                )
            except Exception:  # noqa: BLE001
                logger.warning("stress: reject failed for %s", h.hypothesis_id)
    return {"windows": [str(d.date()) for d, _ in windows], "results": results,
            "rejected": rejected_ids}


def run_variant_backtests(
    *,
    max_per_run: int = 3,
    hypotheses_path: Path = HYPOTHESES_PATH,
    cache_path: Path = CACHE_PATH,
    panel: dict[str, pd.DataFrame] | None = None,
    include_promoted: bool = True,
) -> dict[str, Any]:
    """对所有无缓存的 exploring 变体自动回测 + 晋升.

    Args:
        max_per_run: 每轮最多回测的变体数 (cron 控制耗时).
        hypotheses_path: 假设注册表路径.
        cache_path: 回测缓存路径 (可注入便于测试).
        panel: 预取的 K 线 panel; None → 内部拉取.

    Returns:
        {"backtested": [...], "promoted": [...], "skipped": N}
    """
    registry = HypothesisRegistry(hypotheses_path)
    cache = load_backtest_cache() if cache_path == CACHE_PATH else _read_cache(cache_path)

    # 动态晋升基准: 当前 universe 下基策略的回测 (universe 变化自动重算)
    if panel is None:
        panel = fetch_panel()
    if panel is None:
        return {"backtested": [], "promoted": [], "skipped": 0, "error": "panel fetch failed"}
    base_metrics, base_changed = _load_base_metrics(cache, panel)

    # 基准变化 → 已缓存变体重判晋升状态 (universe 变更后指标不再有效)
    rejudged: list[dict[str, Any]] = []
    if base_changed:
        for h in registry.list():
            sd = str(h.signal_definition)
            if not sd.startswith("combo_variant:") or sd not in cache:
                continue
            m = cache[sd]
            new_status = _promote_status(m, base_metrics)
            if new_status == "testing" and str(h.status) == "exploring":
                try:
                    registry.update(
                        h.hypothesis_id,
                        status="testing",
                        invalidation_notes=(
                            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}: "
                            f"universe 变更后重判 — 年化 {m.get('annual')}% / 夏普 {m.get('sharpe')} "
                            f"双超新基准 ({base_metrics['annual']}%/{base_metrics['sharpe']})"
                        ),
                    )
                    seeded = _auto_seed_strategy(sd, str(h.title))
                    rejudged.append({
                        "hypothesis_id": h.hypothesis_id,
                        "title": h.title,
                        "signal_definition": sd,
                        "status": "testing",
                        "seeded_strategy_id": seeded,
                    })
                except Exception:  # noqa: BLE001
                    logger.warning("variant backtest: rejudge promote failed for %s", h.hypothesis_id)

    candidates: list[tuple[Any, bool]] = []
    for h in registry.list():
        sd = str(h.signal_definition)
        if not sd.startswith("combo_variant:") or sd in cache:
            continue
        st = str(h.status)
        if st == "exploring":
            candidates.append((h, False))
        elif include_promoted and st in ("testing", "validated", "monitoring"):
            # 清缓存/逻辑版本 bump 后, 已晋升变体指标会丢 - 补算但只写
            # 缓存, 不重新流转状态/不播种 (它们已经过晋升闸门).
            candidates.append((h, True))
    if not candidates:
        if cache_path == CACHE_PATH:
            save_backtest_cache(cache)
        else:
            _write_cache(cache_path, cache)
        return {"backtested": [], "promoted": [], "rejudged": rejudged, "skipped": 0}
    if panel is None:
        return {"backtested": [], "promoted": [], "skipped": len(candidates), "error": "panel 数据不足"}

    backtested: list[dict[str, Any]] = []
    promoted: list[dict[str, Any]] = []
    dup_skipped: list[str] = []
    stab_skipped: list[str] = []
    # 全池去重: 因子池 = 注册表所有变体用过的因子 (排除当前候选变体自身)
    pool_factors: list[str] = []
    for h in registry.list():
        sd = str(h.signal_definition)
        if not sd.startswith("combo_variant:"):
            continue
        p = parse_signal_definition(sd)
        if p:
            for f in p["factors"]:
                if f not in pool_factors:
                    pool_factors.append(f)
    for hyp, is_promoted_refill in candidates[:max_per_run]:
        parsed = parse_signal_definition(str(hyp.signal_definition))
        if parsed is None:
            continue
        # 已晋升补算: 跳过去重/稳定性闸门 (已过闸门的旧变体, 只补指标)
        if not is_promoted_refill:
            # 机构实践: 因子相关性去重 - 新增因子与基座/池内因子高相关, 无增量直接否决
            dup = _duplicate_factor_check(panel, parsed["factors"], pool=pool_factors)
            if dup is not None:
                dup_skipped.append(str(hyp.signal_definition))
                try:
                    registry.update(
                        hyp.hypothesis_id,
                        status="rejected",
                        invalidation_notes=(
                            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}: "
                            f"因子去重 - {dup} 与基座因子截面相关 >0.9, 无增量 alpha"
                        ),
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("variant backtest: dup reject failed for %s", hyp.hypothesis_id)
                continue
            # 分时段稳定性闸门: 挖掘因子前/后半段 IC 符号必须一致 (防时变因子/过拟合)
            stab, ic_f, ic_b = _factor_split_ic_check(panel, parsed["factors"])
            if stab is not None:
                stab_skipped.append(str(hyp.signal_definition))
                try:
                    if ic_f is None or ic_b is None:
                        reason = f"{stab} 因子不可用 (模块缺失或 compute 失败)"
                    else:
                        reason = f"{stab} 分时段 IC 符号反转 (前 {ic_f} / 后 {ic_b}), 时变不稳定"
                    registry.update(
                        hyp.hypothesis_id,
                        status="rejected",
                        invalidation_notes=(
                            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}: "
                            f"分时段稳定性 - {reason}"
                        ),
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("variant backtest: stab reject failed for %s", hyp.hypothesis_id)
                continue
        metrics = backtest_variant(
            panel,
            parsed["factors"],
            parsed["weights"],
            parsed["top_n"],
            parsed["bot_n"],
            short_dd_th=parsed.get("short_dd_th"),
            short_mom_th=parsed.get("short_mom_th"),
        )
        if "error" in metrics:
            continue
        metrics["backtested_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cache[str(hyp.signal_definition)] = metrics
        status = _promote_status(metrics, base_metrics)
        record = {
            "hypothesis_id": hyp.hypothesis_id,
            "title": hyp.title,
            "signal_definition": hyp.signal_definition,
            "status": status,
            "metrics": {k: metrics[k] for k in ("annual", "sharpe", "max_dd", "cum")},
        }
        backtested.append(record)
        if status == "testing":
            try:
                registry.update(
                    hyp.hypothesis_id,
                    status="testing",
                    invalidation_notes=(
                        f"{metrics['backtested_at']}: 自动回测年化 {metrics['annual']}% "
                        f"/ 夏普 {metrics['sharpe']} / 回撤 {metrics['max_dd']}%, "
                        f"跑赢基策略 (年化 {BASE_METRICS['annual']}% / 夏普 {BASE_METRICS['sharpe']})"
                    ),
                )
                promoted.append(record)
                # 自动播种为并行策略并直接进模拟盘 (signal_definition 去重)
                seeded = _auto_seed_strategy(str(hyp.signal_definition), str(hyp.title))
                if seeded:
                    record["seeded_strategy_id"] = seeded
                    logger.info(
                        "variant backtest: auto-seeded %s → %s (paper)",
                        hyp.hypothesis_id, seeded,
                    )
            except Exception:  # noqa: BLE001
                logger.warning("variant backtest: promote failed for %s", hyp.hypothesis_id)
        logger.info(
            "variant backtest: %s → %s (annual %s%% sharpe %s)",
            hyp.hypothesis_id, status, metrics["annual"], metrics["sharpe"],
        )

    if cache_path == CACHE_PATH:
        save_backtest_cache(cache)
    else:
        _write_cache(cache_path, cache)
    # 实验日志: 回测轮次结果 (append-only 审计)
    try:
        from src.strategy.experiment_log import log_experiment
        log_experiment(
            "backtest",
            n_backtested=len(backtested), n_promoted=len(promoted),
            n_rejudged=len(rejudged), n_dup_skipped=len(dup_skipped),
            seeded_ids=[r.get("seeded_strategy_id") for r in promoted
                        if r.get("seeded_strategy_id")],
        )
    except Exception:  # noqa: BLE001 — 日志失败绝不阻塞回测
        pass
    return {"backtested": backtested, "promoted": promoted, "rejudged": rejudged,
            "dup_skipped": dup_skipped, "stab_skipped": stab_skipped,
            "skipped": max(0, len(candidates) - max_per_run)}


def _read_cache(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_cache(path: Path, cache: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser(description="变体自动回测 (Loop 进化)")
    parser.add_argument(
        "--max-per-run", type=int, default=20,
        help="每轮最多回测的变体数 (积压多时可调大, 默认 20)",
    )
    parser.add_argument(
        "--stress-only", action="store_true",
        help="只跑压力测试 (场景见 --stress-scenario)",
    )
    parser.add_argument(
        "--stress-scenario", choices=list(STRESS_SCENARIOS), default="crash",
        help="压力场景: crash(默认)/liquidity/flash/macro/all",
    )
    args = parser.parse_args()
    if args.stress_only:
        result = run_stress_test(scenario=args.stress_scenario)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = run_variant_backtests(max_per_run=args.max_per_run)
        result.pop("rejudged", None)
        result["dup_skipped"] = result.get("dup_skipped", [])
        print(json.dumps(result, ensure_ascii=False, indent=2))
