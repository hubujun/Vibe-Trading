"""策略相关性监控 — 持仓重叠 + 因子重叠 + 组合集中度.

回答: 28 个变体是不是其实在赌同一个东西? 组合对单一币的暴露有多大?
数据: strategies.json (signal_definition) + 各策略 state.json (last_longs/shorts)
输出: 最相似策略对 TOP / 因子完全重复对 / 集中度告警 (单币被 >40% 活跃策略持有)

用法: python -m src.strategy.correlation_monitor [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.strategy.variant_backtester import parse_signal_definition  # noqa: E402

RUNTIME_ROOT = Path.home() / ".vibe-trading"
CONC_RATIO = 0.4  # 单币被超过 40% 活跃策略持有 → 告警


def _load_all() -> list[dict]:
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


def _jaccard(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0 if a == b else 0.0
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0


def _factors_of(sd: str) -> set[str]:
    spec = parse_signal_definition(sd)
    return set(spec["factors"]) if spec else set()


def main() -> int:
    ap = argparse.ArgumentParser(description="策略相关性监控")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    items = _load_all()
    active = [it for it in items if it["state"].get("last_longs")]
    n_active = len(active)
    total = len(items)

    # 1. 持仓相似度 (仅活跃策略)
    pos_pairs = []
    for i in range(n_active):
        for j in range(i + 1, n_active):
            a, b = active[i], active[j]
            la, lb = a["state"].get("last_longs", []), b["state"].get("last_longs", [])
            sa_, sb_ = a["state"].get("last_shorts", []), b["state"].get("last_shorts", [])
            jac = _jaccard(la + sa_, lb + sb_)
            pos_pairs.append((round(jac, 3), a["strategy"].get("strategy_id"),
                              b["strategy"].get("strategy_id")))
    pos_pairs.sort(key=lambda x: -x[0])

    # 2. 因子重叠 (全部策略)
    fac_pairs = []
    for i in range(total):
        for j in range(i + 1, total):
            fa = _factors_of(items[i]["strategy"].get("signal_definition", ""))
            fb = _factors_of(items[j]["strategy"].get("signal_definition", ""))
            if fa and fb and fa == fb:
                fac_pairs.append((items[i]["strategy"].get("strategy_id"),
                                  items[j]["strategy"].get("strategy_id"), sorted(fa)))

    # 3. 集中度
    conc = {}
    for it in active:
        for c in it["state"].get("last_longs", []) + it["state"].get("last_shorts", []):
            conc[c] = conc.get(c, 0) + 1
    conc_rank = sorted(conc.items(), key=lambda x: -x[1])
    alerts = [(c, n) for c, n in conc_rank if n_active and n / n_active >= CONC_RATIO]

    if args.json:
        print(json.dumps({
            "active": n_active, "total": total,
            "top_position_similar": pos_pairs[:5],
            "identical_factor_pairs": fac_pairs[:5],
            "concentration": conc_rank, "concentration_alerts": alerts,
        }, ensure_ascii=False, indent=1))
        return 0

    print(f"📊 策略相关性监控 ({n_active}/{total} 活跃)")
    print("─" * 46)
    print("持仓相似度 TOP (多头+空头 Jaccard):")
    for jac, a, b in pos_pairs[:5]:
        print(f"  {jac:.2f}  {a} ↔ {b}")
    if not pos_pairs:
        print("  (无活跃持仓)")
    print("因子完全相同的策略对:")
    for a, b, fs in fac_pairs[:5]:
        print(f"  {a} ↔ {b}  因子: {','.join(fs)}")
    if not fac_pairs:
        print("  (无)")
    print("组合集中度 (币 → 持有策略数):")
    for c, n in conc_rank[:8]:
        flag = " ⚠️" if n_active and n / n_active >= CONC_RATIO else ""
        print(f"  {c:14} {n}/{n_active}{flag}")
    if alerts:
        print(f"⚠️ 集中度告警: 以下币被 >{CONC_RATIO:.0%} 活跃策略持有: "
              + ", ".join(f"{c}({n})" for c, n in alerts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
