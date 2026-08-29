"""组合优化引擎 — 从变体池选低相关策略组合 (毕业时自动跑, 现在即可用).

背景: 20 个活跃策略并行时 OKB 被 20/20 持有 (集中度 100%) — 单币事故整体受损.
组合优化 = 机构做法: 不限制单策略, 而是从候选池选"低相关 + 高夏普"的 K 个策略组合,
并约束组合级暴露 (每币净暴露占比 ≤ 阈值).

输入:
  - 候选池: hypotheses testing/validated/monitoring + strategies.json (signal_definition/持仓)
  - 指标:   ~/.vibe-trading/runs/paper_combo/variant_backtests.json (annual/sharpe/max_dd)
  - 相似度: 持仓 Jaccard (last_longs+last_shorts 快照)
方法: 贪心 — 首选最高夏普; 后续每次选与已选平均相似度最低且夏普达标者;
      加入前校验组合暴露约束 (超限则跳过).

用法:
  python -m src.strategy.portfolio_optimizer [--k 4] [--min-sharpe 0.5]
      [--max-coin-exposure 0.5] [--json]
毕业衔接: 20 笔样本后传入 --nav-weights <file> (策略→模拟盘NAV) 加入评分.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RUNTIME_ROOT = Path.home() / ".vibe-trading"
CACHE = RUNTIME_ROOT / "runs" / "paper_combo" / "variant_backtests.json"
DEFAULT_K = 4          # 对应 top4 变体并行模型
DEFAULT_MIN_SHARPE = 0.5
DEFAULT_MAX_EXP = 0.5  # 组合级每币净暴露 ≤50%


def _load_candidates() -> list[dict]:
    """候选: hypotheses 的 testing/validated/monitoring + strategies 配对."""
    hyps = json.loads((RUNTIME_ROOT / "hypotheses.json").read_text(encoding="utf-8"))
    hyps = hyps if isinstance(hyps, list) else hyps.get("hypotheses", [])
    strs = json.loads((RUNTIME_ROOT / "workbench" / "strategies.json").read_text(encoding="utf-8"))
    strs = strs.get("strategies", []) if isinstance(strs, dict) else strs

    str_by_sd = {s.get("signal_definition"): s for s in strs if s.get("signal_definition")}
    cands = []
    for h in hyps:
        sd = h.get("signal_definition", "")
        if not sd.startswith("combo_variant:"):
            continue
        if h.get("status") not in ("testing", "validated", "monitoring"):
            continue
        s = str_by_sd.get(sd)
        if s is None:
            continue
        st = {}
        sp = Path(s.get("run_dir") or "") / "state.json"
        if sp.exists():
            try:
                st = json.loads(sp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                st = {}
        cands.append({
            "strategy_id": s.get("strategy_id"),
            "title": (h.get("title") or s.get("name") or "")[:40],
            "signal_definition": sd,
            "status": h.get("status"),
            "longs": st.get("last_longs") or [],
            "shorts": st.get("last_shorts") or [],
            "nav": st.get("nav"),
        })
    return cands


def _attach_metrics(cands: list[dict]) -> None:
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        cache = {}
    for c in cands:
        m = cache.get(c["signal_definition"]) or {}
        c["annual"] = m.get("annual")
        c["sharpe"] = m.get("sharpe")
        c["max_dd"] = m.get("max_dd")


def _jaccard(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0


def _coin_exposure(selected: list[dict]) -> dict[str, float]:
    n = len(selected)
    agg: dict[str, float] = {}
    for c in selected:
        for x in c["longs"]:
            agg[x] = agg.get(x, 0) + 1 / n
        for x in c["shorts"]:
            agg[x] = agg.get(x, 0) - 1 / n
    return agg


def _exposure_ok(selected: list[dict], max_exp: float) -> bool:
    exp = _coin_exposure(selected)
    return all(abs(v) <= max_exp + 1e-9 for v in exp.values())


def greedy_select(cands: list[dict], k: int, min_sharpe: float,
                  max_exp: float) -> list[dict]:
    """贪心: 首最高夏普; 之后选与已选平均相似度最低 + 夏普达标 + 暴露约束."""
    pool = [c for c in cands if c.get("sharpe") is not None and c["sharpe"] >= min_sharpe]
    if not pool:
        return []
    pool.sort(key=lambda c: -c["sharpe"])
    selected = [pool[0]]
    pool = pool[1:]
    while len(selected) < k and pool:
        best, best_score = None, -1.0
        for c in pool:
            avg_sim = sum(_jaccard(c["longs"] + c["shorts"],
                                   s["longs"] + s["shorts"]) for s in selected) / len(selected)
            score = 1.0 - avg_sim  # 越低相似 → 越高分
            trial = selected + [c]
            if not _exposure_ok(trial, max_exp):
                continue
            if score > best_score:
                best, best_score = c, score
        if best is None:
            break
        selected.append(best)
        pool.remove(best)
    return selected


def main() -> int:
    ap = argparse.ArgumentParser(description="组合优化引擎")
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--min-sharpe", type=float, default=DEFAULT_MIN_SHARPE)
    ap.add_argument("--max-coin-exposure", type=float, default=DEFAULT_MAX_EXP)
    ap.add_argument("--nav-weights", help="毕业衔接: 策略→NAV JSON 文件 (加入评分)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cands = _load_candidates()
    _attach_metrics(cands)
    if args.nav_weights:
        try:
            navs = json.loads(Path(args.nav_weights).read_text(encoding="utf-8"))
            for c in cands:
                if c["strategy_id"] in navs and navs[c["strategy_id"]] is not None:
                    c["sharpe"] = (c.get("sharpe") or 0) * 0.5 + navs[c["strategy_id"]] * 0.5
        except (OSError, ValueError):
            print("⚠️ nav_weights 读取失败, 忽略")

    valid = [c for c in cands if c.get("sharpe") is not None]
    selected = greedy_select(valid, args.k, args.min_sharpe, args.max_coin_exposure)

    if args.json:
        print(json.dumps({
            "candidates": len(valid), "selected": [
                {k: c[k] for k in ("strategy_id", "title", "status", "sharpe", "annual", "max_dd")}
                for c in selected],
            "coin_exposure": _coin_exposure(selected),
        }, ensure_ascii=False, indent=1))
        return 0

    print(f"🎯 组合优化 (候选 {len(valid)} → 选 {len(selected)}/{args.k}, 夏普≥{args.min_sharpe}, 币暴露≤{args.max_coin_exposure:.0%})")
    print("─" * 60)
    if not selected:
        print("无候选满足条件")
        return 0
    avg_sim = 0.0
    pairs = 0
    for i in range(len(selected)):
        for j in range(i + 1, len(selected)):
            avg_sim += _jaccard(selected[i]["longs"] + selected[i]["shorts"],
                                selected[j]["longs"] + selected[j]["shorts"])
            pairs += 1
    avg_sim = avg_sim / pairs if pairs else 0.0
    avg_sharpe = sum(c["sharpe"] for c in selected) / len(selected)
    avg_annual = sum(c.get("annual") or 0 for c in selected) / len(selected)
    print(f"组合: 平均夏普 {avg_sharpe:.2f} | 平均年化 {avg_annual:.1f}% | 平均持仓相似度 {avg_sim:.2f}")
    print("─" * 60)
    for i, c in enumerate(selected, 1):
        print(f"  {i}. {c['strategy_id']:22} {c['title'][:22]:24} 夏普{c['sharpe']:.2f} "
              f"年化{c.get('annual') or 0:.1f}% 回撤{c.get('max_dd') or 0:.0f}% [{c['status']}]")
    print("─" * 60)
    print("组合级暴露 (每币净暴露):")
    for coin, exp in sorted(_coin_exposure(selected).items(), key=lambda x: -abs(x[1])):
        flag = " ⚠️" if abs(exp) > args.max_coin_exposure else ""
        print(f"  {coin:14} {exp:+.0%}{flag}")
    print("─" * 60)
    print("提示: 毕业评审(20笔样本)时可用 --nav-weights 加入模拟盘实盘表现")
    return 0


if __name__ == "__main__":
    sys.exit(main())
