"""20 笔样本毕业评审 — walk-forward 因子重估 + testing 晋升/降级判定.

触发: 任一策略调仓样本 ≥ 20 笔 (ic_weight_ready_check cron 标记后每周一自动跑).

步骤:
1. 找出 trades ≥ MIN_TRADES 的策略
2. 完整归因每笔 (复用 attribution.attribute_trade) — 每笔的因子截面 IC
3. 因子 IC 汇总: 实盘样本上各因子的 IC 均值 / IC_IR (信息含量实证)
4. walk-forward 重估: 前 60% 样本训练 (IC 归一化 → 建议权重), 后 40% 验证
   (用建议权重合成得分 vs 实际收益的 IC, 对比当前静态权重)
5. 晋升/降级: testing 变体累计表现 vs 基策略 → 建议 validated/monitoring/rejected
   (默认只输出建议, --apply 才写 hypotheses.json)

用法:
  python -m src.strategy.graduation_review           # 只报告
  python -m src.strategy.graduation_review --apply   # 报告 + 应用状态建议
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agent/
from src.strategy.attribution import (  # noqa: E402
    _fetch_panel, _factor_scores, _load_state, _load_strategy, attribute_trade,
)
from src.strategy.variant_backtester import parse_signal_definition  # noqa: E402

RUNTIME_ROOT = Path.home() / ".vibe-trading"
MIN_TRADES = 20
OPEN_ID = "ou_2111554e56d40c5cce1ebab5188bf57b"


def _strategies_with_samples() -> list[dict]:
    raw = json.loads((RUNTIME_ROOT / "workbench" / "strategies.json").read_text(encoding="utf-8"))
    out = []
    for s in raw.get("strategies", []):
        st = _load_state(s)
        n = len(st.get("trades") or [])
        if n >= MIN_TRADES:
            out.append((s, st, n))
    return out


def _per_trade_ic(trades: list[dict], close_df, factor_scores: dict, spec: dict) -> list[dict]:
    """每笔归因 + 因子 IC (得分 vs 区间收益)."""
    out = []
    for t in trades:
        d = pd.Timestamp(t["from"]).date()
        scores_all = {}
        for fid, fs in factor_scores.items():
            row = fs.loc[fs.index.date == d]
            if len(row):
                scores_all[fid] = row.iloc[0]
        out.append(attribute_trade(t, close_df, factor_scores, spec, scores_all))
    return out


def _factor_ic_stats(attribs: list[dict]) -> dict[str, dict]:
    """各因子 IC 均值/IR/正率 (跨样本)."""
    stats: dict[str, list] = {}
    for a in attribs:
        if a.get("error"):
            continue
        for fid, ic in (a.get("factor_ic") or {}).items():
            if ic is None:
                continue
            stats.setdefault(fid, []).append(ic)
    out = {}
    for fid, ics in stats.items():
        s = pd.Series(ics)
        out[fid] = {
            "ic_mean": round(float(s.mean()), 4),
            "ic_ir": round(float(s.mean() / s.std()), 4) if s.std() > 0 else 0.0,
            "ic_pos_rate": round(float((s > 0).mean()), 3),
            "n": len(ics),
        }
    return out


def _walk_forward_weights(attribs: list[dict], spec: dict,
                          ic_stats: dict[str, dict]) -> dict:
    """前 60% 训练: IC 均值归一化 → 建议权重; 返回 {fid: weight}."""
    split = max(1, int(len(attribs) * 0.6))
    train = attribs[:split]
    ic_sum: dict[str, float] = {}
    for a in train:
        if a.get("error"):
            continue
        for fid, ic in (a.get("factor_ic") or {}).items():
            if ic is not None:
                ic_sum[fid] = ic_sum.get(fid, 0.0) + ic
    total = sum(max(0.0, v) for v in ic_sum.values())
    if total <= 0:
        return {}
    w = {fid: max(0.0, v) / total for fid, v in ic_sum.items()}
    # 归一化到与当前权重同量纲 (总和 = 当前权重总和)
    cur_sum = sum(spec["weights"].values()) or 1.0
    return {fid: round(v * cur_sum, 4) for fid, v in w.items()}


def _validate_weights(attribs: list[dict], spec: dict, new_w: dict) -> dict:
    """后 40% 验证: 新权重合成得分 vs 当前权重, 与实际收益的 IC 对比."""
    split = max(1, int(len(attribs) * 0.6))
    valid = attribs[split:]
    if not valid:
        return {"valid_n": 0, "current_ic": None, "new_ic": None}
    cur_ic, new_ic = [], []
    for a in valid:
        if a.get("error") or not a.get("factor_ic"):
            continue
        # 当前权重 IC = 合成得分的实际相关性 (用 factor_ic 加权近似)
        cur = sum((a["factor_ic"].get(fid) or 0.0) * spec["weights"].get(fid, 0.0)
                  for fid in spec["factors"])
        new = sum((a["factor_ic"].get(fid) or 0.0) * new_w.get(fid, 0.0)
                  for fid in spec["factors"])
        cur_ic.append(cur)
        new_ic.append(new)
    return {
        "valid_n": len(cur_ic),
        "current_ic": round(float(pd.Series(cur_ic).mean()), 4) if cur_ic else None,
        "new_ic": round(float(pd.Series(new_ic).mean()), 4) if new_ic else None,
    }


def _grade_testing(attribs: list[dict]) -> dict:
    """testing 变体累计表现 vs 基策略 → 建议状态."""
    cum = sum(a.get("ret", 0.0) for a in attribs if not a.get("error"))
    n = len([a for a in attribs if not a.get("error")])
    if n < MIN_TRADES * 0.6:
        return {"n": n, "cum": cum, "grade": "样本不足"}
    if cum >= 5.0:
        return {"n": n, "cum": cum, "grade": "validated (建议毕业)"}
    if cum >= 0.0:
        return {"n": n, "cum": cum, "grade": "monitoring (继续观察)"}
    return {"n": n, "cum": cum, "grade": "rejected (建议淘汰)"}


def main() -> int:
    ap = argparse.ArgumentParser(description="20 笔样本毕业评审")
    ap.add_argument("--apply", action="store_true", help="应用状态建议到 hypotheses.json")
    args = ap.parse_args()

    ready = _strategies_with_samples()
    if not ready:
        print("样本未达标: 所有策略 trades < 20, 毕业评审待命")
        return 0

    for strategy, state, n in ready:
        sid = strategy.get("strategy_id")
        print("═" * 52)
        print(f"🎓 毕业评审: {strategy.get('name', sid)} ({n} 笔样本)")
        spec = parse_signal_definition(strategy.get("signal_definition", ""))
        if spec is None:
            print("  ERROR: signal_definition 无法解析")
            continue

        close_df, volume_df = _fetch_panel()
        factor_scores = _factor_scores(close_df, volume_df, spec)
        attribs = _per_trade_ic(state.get("trades") or [], close_df, factor_scores, spec)

        # 1. 因子 IC 汇总
        ic_stats = _factor_ic_stats(attribs)
        print("  因子 IC (实盘样本):")
        for fid, st in sorted(ic_stats.items(), key=lambda x: -x[1]["ic_ir"]):
            print(f"    {fid:32} IC={st['ic_mean']:+.3f} IR={st['ic_ir']:+.3f} "
                  f"正率={st['ic_pos_rate']:.0%} (n={st['n']})")

        # 2. walk-forward 重估
        new_w = _walk_forward_weights(attribs, spec, ic_stats)
        print(f"  当前权重: {spec['weights']}")
        if new_w:
            print(f"  建议权重 (前{int(len(attribs)*0.6)}笔训练): {new_w}")
            val = _validate_weights(attribs, spec, new_w)
            if val["valid_n"]:
                delta = (val["new_ic"] or 0) - (val["current_ic"] or 0)
                verdict = "✅ 建议采用" if delta > 0 else "⚠️ 无提升, 保持当前权重"
                print(f"  验证 (后{val['valid_n']}笔): 当前IC={val['current_ic']:+.3f} "
                      f"新IC={val['new_ic']:+.3f} → {verdict}")
        else:
            print("  建议权重: (训练段无正 IC 因子, 保持当前)")

        # 3. 晋升/降级
        grade = _grade_testing(attribs)
        print(f"  累计收益 {grade['cum']:+.2f}% ({grade['n']} 笔) → {grade['grade']}")

        # 实验日志: 毕业评审结果
        try:
            from src.strategy.experiment_log import log_experiment
            log_experiment("graduation", strategy_id=sid, n_samples=n,
                           cum_ret=round(grade["cum"], 3), grade=grade["grade"],
                           ic_summary={k: v["ic_mean"] for k, v in ic_stats.items()},
                           suggested_weights=new_w)
        except Exception:
            pass

        # 4. apply
        if args.apply and grade["grade"].startswith("validated"):
            hyps = json.loads((RUNTIME_ROOT / "hypotheses.json").read_text(encoding="utf-8"))
            changed = 0
            for h in hyps.get("hypotheses", []):
                if h.get("status") == "testing" and h.get("seeded_strategy_id") == sid:
                    h["status"] = "validated"
                    h["note"] = h.get("note", "") + " [毕业评审: 20笔样本达标, validated]"
                    changed += 1
            (RUNTIME_ROOT / "hypotheses.json").write_text(
                json.dumps(hyps, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  --apply: 已更新 {changed} 条 testing → validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
