"""组合暴露报告 — 多策略同时运行时, 每币的组合级暴露.

背景: 每个策略内部有板块上限, 但组合层面(多个策略并行)可能有集中风险 —
      OKB 被 20/20 活跃策略持有 = OKB 出事整个组合一起跌.
本工具把"组合级"暴露摊开: 每币被几个策略持多/持空, 净暴露占比.

用法:
  python -m src.strategy.portfolio_exposure                  # 全部活跃策略
  python -m src.strategy.portfolio_exposure --strategies combo_a,combo_b  # 指定
  python -m src.strategy.portfolio_exposure --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RUNTIME_ROOT = Path.home() / ".vibe-trading"


def _load_items() -> list[dict]:
    raw = json.loads((RUNTIME_ROOT / "workbench" / "strategies.json").read_text(encoding="utf-8"))
    out = []
    for s in raw.get("strategies", []):
        st = {}
        sp = Path(s.get("run_dir") or "") / "state.json"
        if sp.exists():
            try:
                st = json.loads(sp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                st = {}
        out.append({"strategy": s, "state": st})
    return out


def exposure(items: list[dict]) -> dict:
    """组合暴露: 每币 {long_n, short_n, net, net_ratio} (等权策略假设)."""
    n = len(items)
    agg: dict[str, dict] = {}
    for it in items:
        st = it["state"]
        for c in st.get("last_longs") or []:
            a = agg.setdefault(c, {"long_n": 0, "short_n": 0})
            a["long_n"] += 1
        for c in st.get("last_shorts") or []:
            a = agg.setdefault(c, {"long_n": 0, "short_n": 0})
            a["short_n"] += 1
    for c, a in agg.items():
        a["net"] = a["long_n"] - a["short_n"]
        a["net_ratio"] = round(a["net"] / n, 3) if n else 0.0
        a["gross_ratio"] = round((a["long_n"] + a["short_n"]) / n, 3) if n else 0.0
    return {"strategies": n, "coins": agg}


def main() -> int:
    ap = argparse.ArgumentParser(description="组合暴露报告")
    ap.add_argument("--strategies", help="逗号分隔的策略 id 列表 (默认全部活跃)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    items = _load_items()
    if args.strategies:
        want = set(args.strategies.split(","))
        items = [it for it in items if it["strategy"].get("strategy_id") in want]
    active = [it for it in items if it["state"].get("last_longs")]
    if not active:
        print("(无活跃持仓)")
        return 0

    exp = exposure(active)
    coins = sorted(exp["coins"].items(), key=lambda x: -abs(x[1]["net"]))
    n = exp["strategies"]

    if args.json:
        print(json.dumps({"strategies": n, "coins": {c: a for c, a in coins}},
                          ensure_ascii=False, indent=1))
        return 0

    print(f"📦 组合暴露 ({n} 个活跃策略, 等权假设)")
    print("─" * 52)
    print(f"{'币':14} {'多头':>5} {'空头':>5} {'净暴露':>7} {'总暴露':>7}")
    for c, a in coins:
        print(f"{c:14} {a['long_n']:>5} {a['short_n']:>5} {a['net']:+d} "
              f"({a['net_ratio']:+.0%}) {a['gross_ratio']:.0%}")
    worst = [c for c, a in coins if abs(a["net_ratio"]) >= 0.5]
    if worst:
        print(f"⚠️ 净暴露 ≥50% 的币: {', '.join(worst)} — 单币事故将显著冲击整个组合")
    return 0


if __name__ == "__main__":
    sys.exit(main())
