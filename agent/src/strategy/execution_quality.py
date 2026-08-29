"""执行质量层 — OKX API 延迟采样 + 信号执行闭环健康.

回答: 行情链路快不快? 信号有没有按时生成? 记账闭环有没有滞后?
数据: OKX public/time 延迟采样(走 ClashX 代理) · 各策略 state.json 新鲜度 ·
      最近 trade 的 to 日期 vs 今天 (调仓滞后)

用法: python -m src.strategy.execution_quality [--json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

RUNTIME_ROOT = Path.home() / ".vibe-trading"
PROXY = "http://127.0.0.1:7890"


def _okx_latency(samples: int = 3) -> list[float]:
    """OKX public/time 延迟采样 (走代理). 返回各次秒数."""
    lats = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": PROXY}))
    for _ in range(samples):
        t0 = time.time()
        try:
            req = urllib.request.Request(
                "https://www.okx.com/api/v5/public/time",
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            )
            with opener.open(req, timeout=10) as r:
                r.read()
            lats.append(round((time.time() - t0) * 1000, 1))
        except Exception:
            lats.append(None)
        time.sleep(0.3)
    return lats


def _signal_freshness() -> list[dict]:
    """各策略 state.json 新鲜度 (mtime 距今小时数) + 最近 trade 滞后天数."""
    out = []
    raw = json.loads((RUNTIME_ROOT / "workbench" / "strategies.json").read_text(encoding="utf-8"))
    now = time.time()
    today = date.today()
    for s in raw.get("strategies", []):
        sp = Path(s.get("run_dir") or "") / "state.json"
        if not sp.exists():
            continue
        age_h = round((now - sp.stat().st_mtime) / 3600, 1)
        lag_d = None
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
            trades = st.get("trades") or []
            if trades:
                to = date.fromisoformat(trades[-1]["to"])
                lag_d = (today - to).days
        except (OSError, ValueError):
            pass
        out.append({"strategy_id": s.get("strategy_id"), "age_h": age_h, "lag_d": lag_d})
    out.sort(key=lambda x: -x["age_h"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="执行质量层")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    lats = _okx_latency()
    ok = [x for x in lats if x is not None]
    med = statistics.median(ok) if ok else None

    freshness = _signal_freshness()
    stale = [f for f in freshness if f["age_h"] > 26]  # 信号每天 07:00, >26h 未更新=异常
    lagging = [f for f in freshness if f["lag_d"] is not None and f["lag_d"] > 2]

    if args.json:
        print(json.dumps({
            "okx_latency_ms": lats, "okx_median_ms": med,
            "stale_signals": stale, "lagging_accounting": lagging,
        }, ensure_ascii=False, indent=1))
        return 0

    print("⚡ 执行质量层")
    print("─" * 46)
    if med is not None:
        flag = "✅" if med < 2000 else "⚠️"
        print(f"OKX API 延迟 (走代理): 中位数 {med:.0f}ms {flag}  采样: {lats}")
    else:
        print("OKX API 延迟: 全部超时 ⚠️ (代理/网络问题)")
    print("信号新鲜度 (state.json 更新距今小时):")
    for f in freshness[:5]:
        flag = "⚠️" if f["age_h"] > 26 else ""
        lag_s = f" 调仓滞后 {f['lag_d']}d" if f["lag_d"] is not None else ""
        print(f"  {f['strategy_id']:24} {f['age_h']:>6}h{lag_s} {flag}")
    if stale:
        print(f"⚠️ {len(stale)} 个策略信号过期 (>26h): " + ", ".join(s["strategy_id"] for s in stale))
    if lagging:
        print(f"⚠️ {len(lagging)} 个策略调仓滞后 (>2d): " + ", ".join(s["strategy_id"] for s in lagging))
    if not stale and not lagging:
        print("✅ 信号与记账闭环健康")
    return 0


if __name__ == "__main__":
    sys.exit(main())
