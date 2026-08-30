#!/usr/bin/env python3
"""回填重建策略的真实回测指标 (2026-08-30).

背景: 8/23 strategies.json 数据恢复时, 20 条策略从注册表重建,
strategy_backtest 填的是基准占位值 (37.93%/1.2) — 它们从未用 v2 逻辑算过.
本脚本: 对每条重建策略的 signal_definition 跑 backtest_variant,
回填 strategy_backtest (完整列表写回) + 补写 variant_backtests.json 缓存.

用法: cd ~/Vibe-Trading/agent && ~/Vibe-Trading/.venv/bin/python scripts/backfill_rebuilt_metrics.py
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agent/

from src.strategy.variant_backtester import (  # noqa: E402
    backtest_variant, fetch_panel, parse_signal_definition, save_backtest_cache,
)

WB = Path.home() / ".vibe-trading" / "workbench" / "strategies.json"
CACHE = Path.home() / ".vibe-trading" / "runs" / "paper_combo" / "variant_backtests.json"


def main() -> int:
    raw = json.loads(WB.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    bt_items = {k: v for k, v in cache.items() if k != "meta"}

    rebuilt = [
        s for s in raw["strategies"]
        if (s.get("phase_history") or [{}])[0].get("note", "").startswith("重建")
    ]
    todo = [s for s in rebuilt if not any(
        abs(((s.get("strategy_backtest") or {}).get("sharpe") or 0) - v.get("sharpe", 0)) > 0.01
        for k, v in bt_items.items() if k == s.get("signal_definition")
    )]

    print(f"重建策略 {len(rebuilt)} 条, 需回填 {len(todo)} 条")
    if not todo:
        return 0

    print("拉面板中 (17币 800天, 约2-3分钟)...")
    panel = fetch_panel()
    if panel is None:
        print("ERROR: panel fetch failed")
        return 1

    done, failed = 0, 0
    for s in todo:
        sd = s.get("signal_definition", "")
        parsed = parse_signal_definition(sd)
        if parsed is None:
            print(f"  ✗ {s['strategy_id'][:16]} sd 解析失败")
            failed += 1
            continue
        try:
            m = backtest_variant(panel, parsed["factors"], parsed["weights"],
                                 parsed["top_n"], parsed["bot_n"])
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {s['strategy_id'][:16]} 回测异常: {str(exc)[:60]}")
            failed += 1
            continue
        if "error" in m:
            print(f"  ✗ {s['strategy_id'][:16]} {m['error']}")
            failed += 1
            continue
        new_bt = {
            "annual": m.get("annual"), "sharpe": m.get("sharpe"),
            "max_dd": m.get("max_dd"), "cum": m.get("cum"),
            "backtested_at": m.get("backtested_at"),
        }
        s["strategy_backtest"] = new_bt
        # 同时补写缓存 (完整列表, 数据安全铁律)
        if sd not in bt_items:
            bt_items[sd] = m
            bt_items[sd]["backtested_at"] = m.get("backtested_at")
        done += 1
        print(f"  ✓ {s['strategy_id'][:16]} 年化 {m.get('annual')}% / 夏普 {m.get('sharpe')} / 回撤 {m.get('max_dd')}%")

    # 完整列表写回
    payload = {"meta": cache.get("meta", {}), **bt_items}
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE)
    json.dump(raw, open(WB, "w"), ensure_ascii=False, indent=2)
    print(f"\n完成: 回填 {done} 条, 失败 {failed} 条")

    # 验证
    raw2 = json.loads(WB.read_text(encoding="utf-8"))
    n_base = sum(1 for s in raw2["strategies"]
                 if (s.get("phase_history") or [{}])[0].get("note", "").startswith("重建")
                 and abs(((s.get("strategy_backtest") or {}).get("sharpe") or 0) - 1.2) < 0.01)
    print(f"验证: 重建策略中仍显示基准值(1.2)的: {n_base} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
