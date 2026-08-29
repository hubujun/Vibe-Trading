"""实验日志库 — Loop 所有决策的 append-only JSONL 审计日志.

记录点: 变体回测/晋升播种 (variant_backtester) · 调仓记账 (daily_signal) ·
       毕业评审 (graduation_review) · 归因 (attribution)
文件: ~/.vibe-trading/experiments.jsonl (只追加不覆盖, 历史不可篡改)
查询: python -m src.strategy.experiment_log [--last N] [--type X] [--strategy S]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path.home() / ".vibe-trading" / "experiments.jsonl"


def log_experiment(kind: str, **payload) -> None:
    """追加一条实验记录. 失败静默 — 日志绝不能阻塞交易/回测主流程."""
    try:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def query(kind: str | None = None, strategy: str | None = None,
          limit: int = 20) -> list[dict]:
    """读取最近 N 条记录 (可选按 kind / strategy_id 过滤)."""
    out: list[dict] = []
    try:
        for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if kind and e.get("kind") != kind:
                continue
            if strategy and e.get("strategy_id") != strategy:
                continue
            out.append(e)
    except OSError:
        pass
    return out[-limit:]


def main() -> int:
    ap = argparse.ArgumentParser(description="实验日志库查询")
    ap.add_argument("--last", type=int, default=20, help="最近 N 条 (默认 20)")
    ap.add_argument("--type", dest="kind", help="按 kind 过滤 (backtest/trade/graduation/...)")
    ap.add_argument("--strategy", help="按 strategy_id 过滤")
    args = ap.parse_args()

    entries = query(args.kind, args.strategy, args.last)
    if not entries:
        print("(无记录)")
        return 0
    for e in entries:
        ts = e.get("ts", "")[11:19]
        kind = e.get("kind", "?")
        sid = e.get("strategy_id", "")
        detail = {k: v for k, v in e.items() if k not in ("ts", "kind", "strategy_id")}
        print(f"[{ts}] {kind:12} {sid:20} {json.dumps(detail, ensure_ascii=False)[:120]}")
    print(f"--- {len(entries)} 条 (共 {sum(1 for _ in LOG_PATH.open() if _.strip()) if LOG_PATH.exists() else 0} 条累计)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
