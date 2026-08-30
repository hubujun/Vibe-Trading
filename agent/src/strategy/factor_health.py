"""因子体检 — 全因子 IC / IC_IR / 分层收益评估 + 模拟盘对照.

回答"基策略/变体因子选得好不好": 每个因子的横截面信息含量
(IC=排序能力, IC_IR=稳定性, IC+率, 多空分层收益) + 各策略模拟盘净值对照.

结果缓存 ~/.vibe-trading/factor_health.json (6 小时过期, GET 不重算).
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.strategy.variant_backtester import (
    ACADEMIC_MODULES,
    SECTOR,
    _row_zscore,
    _sector_cap,
    fetch_panel,
    load_factor_module,
)

CACHE_PATH = Path.home() / ".vibe-trading" / "factor_health.json"
CACHE_TTL_SECONDS = 6 * 3600

#: 挖掘池 zoo 根目录 (crypto_mined 因子文件所在)
_ZOO_ROOT = Path(__file__).resolve().parent.parent / "factors" / "zoo" / "crypto_mined"


def _list_mined_factors() -> list[str]:
    """扫描挖掘池 zoo: 全部因子文件 stem (排除 _ 前缀与 __pycache__).

    动态扫描而非硬编码清单 — 因子挖掘器持续产出新因子, 硬编码会漏掉。
    僵尸因子 (文件缺失/依赖列缺失 compute 失败) 由 _evaluate 自然跳过。
    """
    if not _ZOO_ROOT.is_dir():
        return []
    return sorted(
        p.stem for p in _ZOO_ROOT.glob("*.py") if not p.name.startswith("_")
    )


#: 评估的因子清单: 学术因子 + 挖掘池 zoo 全量 + 市场状态因子
#: (只含能真实加载+compute 的因子 — 2026-08-30 排查: volume_price_corr_regime /
#:  volume_volatility_scaled 因子文件不存在(8-23 数据恢复重建的僵尸策略, 信号实际
#:  降级为 BAB+high52w 双因子); volume_flow_momentum / volume_close_location 依赖
#:  panel['high'] 但全链路面板只有 close+volume → KeyError。纯版 volume_price_corr
#:  因子文件存在、compute 正常、回测 1.27 夏普, 补入清单。)
FACTORS = list(ACADEMIC_MODULES.keys()) + _list_mined_factors()


def _evaluate(fid: str, panel: dict) -> dict[str, Any] | None:
    """单因子: 滚动截面 IC / IC_IR / IC+率 / 多空分层收益 (top3-bot3 含成本)."""
    mod = load_factor_module(fid)
    if mod is None:
        print(f"  ⚠️ factor_health: 因子 {fid} 模块加载失败, 跳过")
        return None
    close = panel["close"]
    try:
        f = mod.compute(panel).reindex(close.index)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ factor_health: 因子 {fid} compute 失败: {str(exc)[:80]}")
        return None
    if fid not in ACADEMIC_MODULES:
        f = _row_zscore(f)
    rets = close.pct_change().shift(-1)
    fr = f.rank(axis=1)
    rr = rets.rank(axis=1)
    ic = fr.corrwith(rr, axis=1).dropna()
    ic = ic[ic.abs() < 1]
    if len(ic) < 100:
        return None
    ic_mean = float(ic.mean())
    ic_ir = float(ic.mean() / (ic.std() + 1e-9))
    ic_pos = float((ic > 0).mean())
    # 时效性: 最近 30/60 交易日滚动 IC (因子失效预警 — 全窗口 IC 掩盖近期衰减)
    ic_30d = float(ic.tail(30).mean()) if len(ic) >= 30 else None
    ic_60d = float(ic.tail(60).mean()) if len(ic) >= 60 else None
    if ic_30d is None:
        ic_trend = "insufficient"
    elif ic_30d < 0:
        ic_trend = "decaying"          # 近期已失效
    elif ic_mean > 0 and ic_30d < ic_mean * 0.5:
        ic_trend = "weakening"         # 显著弱于历史
    else:
        ic_trend = "stable"
    # 多空分层收益 (信号日 t-1 选币 → t 日收益, 含 0.2% 双边成本)
    rets_d = close.pct_change()
    ls_ret: list[float] = []
    for i in range(1, len(close)):
        dt_prev, dt = close.index[i - 1], close.index[i]
        row = f.loc[dt_prev].dropna().sort_values(ascending=False)
        if len(row) < 6:
            continue
        longs = _sector_cap(row.index.tolist(), 3)
        shorts = _sector_cap(row.index.tolist()[::-1], 3)
        r = rets_d.loc[dt]
        ls_ret.append(float(r[longs].mean() - r[shorts].mean() - 0.002))
    if not ls_ret:
        return None
    ls = pd.Series(ls_ret)
    nav = (1 + ls).cumprod()
    if nav.iloc[-1] <= 0:
        ls_annual = float("nan")
    else:
        ls_annual = float(nav.iloc[-1]) ** (365 / len(nav)) - 1
    return {
        "factor": fid,
        "ic": round(ic_mean, 4),
        "ic_ir": round(ic_ir, 3),
        "ic_pos": round(ic_pos, 3),
        "ic_30d": round(ic_30d, 4) if ic_30d is not None else None,
        "ic_60d": round(ic_60d, 4) if ic_60d is not None else None,
        "ic_trend": ic_trend,
        "ls_annual": None if math.isnan(ls_annual) else round(ls_annual * 100, 1),
        "ls_sharpe": round(float(ls.mean() / (ls.std() + 1e-9) * math.sqrt(365)), 2),
        "days": len(ls),
    }


def _paper_nav_by_factor() -> dict[str, float]:
    """模拟盘对照: 每个因子在已播种策略里的最佳净值."""
    wb = Path.home() / ".vibe-trading" / "workbench" / "strategies.json"
    best: dict[str, float] = {}
    try:
        raw = json.loads(wb.read_text(encoding="utf-8"))
        for s in raw.get("strategies", []):
            run_dir = Path(s.get("run_dir") or "")
            st = run_dir / "state.json"
            if not st.exists():
                continue
            state = json.loads(st.read_text(encoding="utf-8"))
            nav = float(state.get("nav") or 1.0)
            for fid in (s.get("factors") or []):
                best[fid] = max(best.get(fid, 0.0), nav)
    except (OSError, ValueError, TypeError):
        pass
    return best


def compute_factor_health(force: bool = False) -> dict[str, Any]:
    """因子体检结果 (缓存 6h). force=True 强制重算."""
    if not force and CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            age = time.time() - float(cached.get("_ts", 0))
            if age < CACHE_TTL_SECONDS:
                return {k: v for k, v in cached.items() if k != "_ts"}
        except (OSError, ValueError, TypeError):
            pass
    panel = fetch_panel()
    if panel is None:
        return {"error": "panel fetch failed", "results": []}
    results = []
    for fid in FACTORS:
        r = _evaluate(fid, panel)
        if r:
            results.append(r)
    results.sort(key=lambda x: x["ic_ir"], reverse=True)
    paper = _paper_nav_by_factor()
    for r in results:
        r["paper_best_nav"] = round(paper.get(r["factor"], 1.0), 4)
    payload = {
        "_ts": time.time(),
        "universe_size": panel["close"].shape[1],
        "days": panel["close"].shape[0],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results": results,
    }
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return {k: v for k, v in payload.items() if k != "_ts"}
