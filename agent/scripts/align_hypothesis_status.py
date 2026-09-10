"""假设状态对齐 — 把"在跑模拟盘"却被标记 rejected 的策略假设纠正回 testing.

背景 (2026-09-10)
-----------------
review_engine 的旧口径「testing + 连亏 ≥3 笔 → rejected」把大量正常在跑的策略
永久否决: 市场中性策略 3 天小幅连亏 (合计可低至 -0.7%) 属随机波动, 实测
28/37 条在跑策略被标 rejected —— 于是毕业评审/压力测试/候选组合面板全部
按 testing 过滤, 这些策略到 20 笔也升不了级。

本脚本只做**状态对齐** (不改净值/调仓/参数):
    strategies.json 里 phase=paper (在跑) 且有 signal_definition
      → 注册表同 signal_definition 的假设若 status=rejected → 改 testing

用法:
    python scripts/align_hypothesis_status.py --dry-run   # 只报告
    python scripts/align_hypothesis_status.py             # 应用
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agent/

from src.hypotheses.registry import HypothesisRegistry  # noqa: E402

RUNTIME_ROOT = Path.home() / ".vibe-trading"
RUNNING_PHASES = ("paper", "live")


def _load_strategies() -> list[dict]:
    raw = json.loads(
        (RUNTIME_ROOT / "workbench" / "strategies.json").read_text(encoding="utf-8")
    )
    return raw.get("strategies", []) if isinstance(raw, dict) else raw


def _trades_count(strategy: dict) -> int:
    for key in ("run_dir",):
        rd = strategy.get(key)
        if not rd:
            continue
        st = Path(rd) / "state.json"
        if st.exists():
            try:
                return len(json.loads(st.read_text(encoding="utf-8")).get("trades") or [])
            except (OSError, ValueError, TypeError):
                return 0
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="在跑策略的假设状态对齐")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写回")
    args = ap.parse_args()

    strategies = _load_strategies()
    running = {
        str(s.get("signal_definition") or ""): s
        for s in strategies
        if s.get("phase") in RUNNING_PHASES and s.get("signal_definition")
    }
    print(f"在跑策略 (phase in {RUNNING_PHASES}): {len(running)} 条有 signal_definition")

    registry = HypothesisRegistry(RUNTIME_ROOT / "hypotheses.json")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fixed, skipped = [], 0
    for hyp in registry.list():
        if hyp.status != "rejected":
            continue
        strategy = running.get(str(hyp.signal_definition or ""))
        if strategy is None:
            skipped += 1  # 未进模拟盘的假设, 否决与模拟盘无关 → 不动
            continue
        sid = strategy.get("strategy_id")
        n = _trades_count(strategy)
        note = (
            f"{stamp}: 纠正 — 策略 {sid} 在模拟盘运行中 ({n} 笔调仓), "
            f"注册表应为验证中 (非否决); 触发自 align_hypothesis_status.py"
        )
        fixed.append((hyp.hypothesis_id, sid, n, hyp.title))
        if not args.dry_run:
            prev = str(hyp.invalidation_notes or "")
            registry.update(
                hyp.hypothesis_id,
                status="testing",
                invalidation_notes=f"{prev}\n{note}" if prev else note,
            )

    for hid, sid, n, title in fixed:
        print(f"  {'[dry]' if args.dry_run else '[fix]'} {hid} {sid} ({n} 笔) {title[:44]}")
    print(f"\n纠正 {len(fixed)} 条 | 跳过 (未进模拟盘) {skipped} 条")
    if args.dry_run:
        print("(dry-run: 未写回)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
