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

#: 币种 universe (与 combo_backtest/daily_signal 一致)
SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
    "DOGE-USDT", "OKB-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT",
]
DAYS = 800
COST = 0.001
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

#: academic 因子 → 模块名映射
ACADEMIC_MODULES: dict[str, str] = {"BAB": "bab", "RMW": "rmw", "high52w": "high52w"}


# ============================================================================
# signal_definition 解析
# ============================================================================


def parse_signal_definition(sig_def: str) -> dict[str, Any] | None:
    """解析 ``combo_variant: factors=[...] weights={...} top_n=3 bot_n=3``.

    返回 {factors, weights, top_n, bot_n}; 无法解析返回 None.
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

    if not factors or not weights:
        return None
    return {"factors": factors, "weights": weights, "top_n": top_n, "bot_n": bot_n}


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
        params = {"instId": symbol, "bar": "1D", "limit": "100"}
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
    df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms")
    df = df.set_index("ts")
    df["close"] = df["close"].astype(float)
    df["volume"] = df["vol"].astype(float)
    return df[["close", "volume"]]


def fetch_panel() -> dict[str, pd.DataFrame] | None:
    """拉 10 币 panel (close + volume), 对齐后返回; 数据不足返回 None."""
    frames: dict[str, pd.DataFrame] = {}
    for s in SYMBOLS:
        df = fetch_okx_daily(s)
        if df is None:
            return None
        frames[s] = df
        time.sleep(0.3)
    close = pd.DataFrame({s: f["close"] for s, f in frames.items()})
    volume = pd.DataFrame({s: f["volume"] for s, f in frames.items()})
    close = close.dropna(axis=1, how="all").ffill().dropna()
    volume = volume.reindex(close.index).ffill().dropna()
    if close.shape[0] < 300 or close.shape[1] < 4:
        return None
    return {"close": close, "volume": volume}


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
) -> dict[str, Any]:
    """对单个变体跑回测, 返回指标 dict."""
    close = panel["close"]
    rets = close.pct_change()

    # 因子得分合成 (缺失因子 → 该因子权重摊到其他因子)
    mods = {fid: load_factor_module(fid) for fid in factors}
    valid = {fid: m for fid, m in mods.items() if m is not None}
    if not valid:
        return {"error": "no valid factors"}
    score_sum = None
    weight_sum = 0.0
    for fid, mod in valid.items():
        try:
            f = _row_zscore(mod.compute(panel).reindex(close.index))
        except Exception:  # noqa: BLE001
            continue
        w = float(weights.get(fid, 0.0))
        if w <= 0:
            continue
        score_sum = f * w if score_sum is None else score_sum + f * w
        weight_sum += w
    if score_sum is None or weight_sum <= 0:
        return {"error": "no usable factors"}
    combo = score_sum / weight_sum

    # 多 top_n 空 bot_n (head/tail 精确截取)
    r = combo.rank(axis=1, method="first")
    n = close.shape[1]
    top_n_eff = min(top_n, n)
    bot_n_eff = min(bot_n, n)
    long_mask = r > n - top_n_eff
    short_mask = r <= bot_n_eff
    w = long_mask.astype(float) - short_mask.astype(float)
    w = w.div(w.abs().sum(axis=1), axis=0)

    daily = (w.shift(1) * rets).sum(axis=1)
    turnover = w.diff().abs().sum(axis=1) / 2
    net = daily - turnover * COST
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


def load_backtest_cache() -> dict[str, dict[str, Any]]:
    """读变体回测缓存 (fail-open)."""
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_backtest_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE_PATH)


# ============================================================================
# 晋升规则
# ============================================================================


def _promote_status(metrics: dict[str, Any]) -> str:
    """晋升判定: 年化&夏普双超 + 回撤可控 → testing; 否则保持 exploring."""
    annual = metrics.get("annual")
    sharpe = metrics.get("sharpe")
    max_dd = metrics.get("max_dd")
    if annual is None or sharpe is None or max_dd is None:
        return "exploring"
    if (
        annual > BASE_METRICS["annual"]
        and sharpe > BASE_METRICS["sharpe"]
        and max_dd > BASE_METRICS["max_dd"] * DD_TOLERANCE
    ):
        return "testing"
    return "exploring"


# ============================================================================
# 主入口
# ============================================================================


def run_variant_backtests(
    *,
    max_per_run: int = 3,
    hypotheses_path: Path = HYPOTHESES_PATH,
    cache_path: Path = CACHE_PATH,
    panel: dict[str, pd.DataFrame] | None = None,
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

    candidates = [
        h for h in registry.list()
        if str(h.status) == "exploring" and str(h.signal_definition).startswith("combo_variant:")
        and h.signal_definition not in cache
    ]
    if not candidates:
        return {"backtested": [], "promoted": [], "skipped": 0}

    if panel is None:
        panel = fetch_panel()
    if panel is None:
        return {"backtested": [], "promoted": [], "skipped": len(candidates), "error": "panel 数据不足"}

    backtested: list[dict[str, Any]] = []
    promoted: list[dict[str, Any]] = []
    for hyp in candidates[:max_per_run]:
        parsed = parse_signal_definition(str(hyp.signal_definition))
        if parsed is None:
            continue
        metrics = backtest_variant(
            panel,
            parsed["factors"],
            parsed["weights"],
            parsed["top_n"],
            parsed["bot_n"],
        )
        if "error" in metrics:
            continue
        metrics["backtested_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cache[str(hyp.signal_definition)] = metrics
        status = _promote_status(metrics)
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
    return {"backtested": backtested, "promoted": promoted, "skipped": max(0, len(candidates) - max_per_run)}


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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = run_variant_backtests()
    print(json.dumps(result, ensure_ascii=False, indent=2))
