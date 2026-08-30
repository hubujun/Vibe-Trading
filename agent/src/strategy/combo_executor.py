"""combo 策略实盘执行器 — 信号 → OKX 永续下单 → 风控门 → 对账 (2026-08-30).

实盘链路: 工作台组合策略 (daily_signal 07:00 信号) 通过本执行器真实下单.
与模拟盘同源信号 (build_signal), 实盘=模拟盘同一套逻辑.

流程 (建议 cron 07:05):
1. 读 strategies.json 选中的实盘策略 (默认基策略 combo_bab_52w)
2. 调 daily_signal.build_signal 拿最新信号 (longs/shorts 目标持仓)
3. 每腿名义 = 分配资金 ÷ 腿数 (资金来自 mandate hard_caps, 单源)
4. 查 OKX 当前永续持仓 → 差异生成订单 (开/平)
5. 风控门: 规则引擎 (宏观静默/连亏停/日内熔断) + kill switch + mandate 上限
6. 市价下单 (USDT-SWAP 永续) + 对账记录 (成交价 vs 信号价 → 滑点)

用法:
  python -m src.strategy.combo_executor --dry-run   # 只打印计划
  python -m src.strategy.combo_executor --live      # 真实下单 (默认)
  python -m src.strategy.combo_executor --sid combo_bab_52w
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RUNTIME_ROOT = Path.home() / ".vibe-trading"
STRATEGIES_PATH = RUNTIME_ROOT / "workbench" / "strategies.json"
ORDER_LEDGER = RUNTIME_ROOT / "live_exec" / "orders.jsonl"
EXEC_STATE = RUNTIME_ROOT / "live_exec" / "state.json"

#: 币 → 永续 instId (combo 全部用 USDT 永续, 支持做空)
PERP_INST: dict[str, str] = {}


def _perp_inst(symbol: str) -> str:
    """'OKB-USDT' → 'OKB-USDT-SWAP' (永续)."""
    s = symbol.strip().upper()
    if not s.endswith("-SWAP"):
        return f"{s}-SWAP"
    return s


def _quote_symbol(inst_id: str) -> str:
    """'OKB-USDT-SWAP' → 'OKB-USDT'."""
    return inst_id.replace("-SWAP", "")


def load_strategy(strategy_id: str) -> dict | None:
    try:
        raw = json.loads(STRATEGIES_PATH.read_text(encoding="utf-8"))
        for s in raw.get("strategies", []):
            if s["strategy_id"] == strategy_id and s.get("phase") != "paused":
                return s
    except (OSError, ValueError, TypeError):
        pass
    return None


def allocation_per_leg() -> float:
    """每腿名义资金 = mandate 总敞口上限 ÷ 6 (最多 6 条腿, 等权)."""
    try:
        from src.live.mandate.store import load_mandate
        mandate = load_mandate("okx")
        if mandate:
            total = float(mandate.hard_caps.max_total_exposure_usd)
            if total > 0:
                return round(total / 6.0, 2)
    except Exception:  # noqa: BLE001
        pass
    return 250.0  # 兜底: 1500U/6


def fetch_current_positions() -> dict[str, dict]:
    """OKX 当前永续持仓: {instId: {side, size, notional}}."""
    try:
        from src.trading.connectors.okx import sdk as okx_sdk
        from src.trading.connectors.okx.sdk import load_config
        raw = okx_sdk.get_positions(load_config())
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    items = raw.get("data") if isinstance(raw, dict) else raw
    for p in items or []:
        inst = p.get("instId") or ""
        if not inst.endswith("-SWAP"):
            continue
        pos = float(p.get("pos") or 0)
        if abs(pos) < 1e-9:
            continue
        out[inst] = {
            "side": "long" if pos > 0 else "short",
            "size": abs(pos),
            "notional": float(p.get("notionalUsd") or 0),
        }
    return out


def build_orders(target_longs: list[str], target_shorts: list[str],
                 current: dict[str, dict], per_leg_long: float,
                 per_leg_short: float | None = None) -> list[dict]:
    """目标持仓 vs 当前持仓 → 订单列表 (开/平).

    规则: 目标有而实际无 → 开仓; 实际有而目标无 → 平仓; 都有 → 不动.
    开仓名义: 多头腿 = per_leg_long, 空头腿 = per_leg_short (默认同 per_leg_long) —
    风控乘数 (事件/regime/波动率目标/疯牛保险) 已随 build_signal 的 long_mult/short_mult 缩放.
    返回订单: {symbol(永续), side(buy/sell), notional, action(open/close), target(long/short)}
    """
    orders: list[dict] = []
    per_leg_short = per_leg_short if per_leg_short is not None else per_leg_long
    target_insts = {_perp_inst(s) for s in target_longs} | {_perp_inst(s) for s in target_shorts}
    target_side = {_perp_inst(s): "long" for s in target_longs}
    target_side.update({_perp_inst(s): "short" for s in target_shorts})
    per_leg_by_side = {"long": per_leg_long, "short": per_leg_short}

    for inst in sorted(target_insts):
        want = target_side[inst]
        cur = current.get(inst)
        if cur is None:
            # 开仓
            side = "buy" if want == "long" else "sell"
            orders.append({
                "symbol": inst, "side": side, "notional": per_leg_by_side[want],
                "action": "open", "target": want,
            })
        # 已有持仓且方向一致 → 不动 (等权固定, 不调权重)

    for inst, cur in current.items():
        if inst not in target_insts:
            # 平仓 (反向)
            side = "sell" if cur["side"] == "long" else "buy"
            orders.append({
                "symbol": inst, "side": side,
                "notional": cur["notional"] or per_leg_long,
                "action": "close", "target": "flat",
            })
    return orders


def risk_gate(now_local=None) -> tuple[bool, str | None]:
    """风控门: kill switch + 规则引擎 (静默/连亏/熔断). 返回 (放行, 拦截原因)."""
    from src.crypto_autopilot.live_executor import halt_flag_set
    if halt_flag_set("okx"):
        return False, "kill switch 已触发"
    from src.crypto_autopilot.rules_engine import RuleState, evaluate
    from src.strategy.macro_events import events_on
    state = RuleState.load()
    events = []
    try:
        events = events_on() or []
    except Exception:  # noqa: BLE001
        pass
    verdict = evaluate(state=state, equity_now=None, closed_trades_today=[], events=events)
    if not verdict.can_trade:
        return False, verdict.reason
    if verdict.action == "halt":
        return False, verdict.reason
    return True, None


def execute_orders(orders: list[dict], dry_run: bool = True) -> list[dict]:
    """逐笔下单 (市价, 重试 2 次), 记录对账."""
    results: list[dict] = []
    if dry_run or not orders:
        for o in orders:
            results.append({**o, "status": "planned" if dry_run else "noop", "ts": _now_iso()})
        return results
    from src.trading.connectors.okx import sdk as okx_sdk
    from src.trading.connectors.okx.sdk import load_config
    cfg = load_config()
    for o in orders:
        outcome = {"status": "error", "error": "max retries"}
        for attempt in range(3):
            try:
                r = okx_sdk.place_order(
                    cfg, symbol=o["symbol"], side=o["side"],
                    notional=o["notional"], order_type="market",
                )
                if r.get("status") == "ok":
                    outcome = {
                        "status": "ok", "order_id": r.get("order_id"),
                        "fill_price": r.get("avg_fill_price") or r.get("price"),
                        "attempts": attempt + 1,
                    }
                    break
                outcome = {"status": "error", "error": r.get("error", str(r))[:120]}
            except Exception as exc:  # noqa: BLE001
                outcome = {"status": "error", "error": str(exc)[:120]}
        results.append({**o, **outcome, "ts": _now_iso()})
    return results


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_ledger(orders: list[dict]) -> None:
    try:
        EXEC_STATE.parent.mkdir(parents=True, exist_ok=True)
        with open(ORDER_LEDGER, "a", encoding="utf-8") as f:
            for o in orders:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="combo 策略实盘执行器")
    ap.add_argument("--sid", default="combo_bab_52w", help="策略 id (默认基策略)")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划, 不下单")
    args = ap.parse_args()

    strategy = load_strategy(args.sid)
    if strategy is None:
        print(f"ERROR: 策略不存在或已暂停: {args.sid}")
        return 1

    # 1. 信号 (与模拟盘同源)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from daily_signal import build_signal
    sig = build_signal(strategy)
    if "error" in sig:
        print(f"ERROR: 信号生成失败: {sig['error']}")
        return 1
    longs, shorts = sig.get("longs", []), sig.get("shorts", [])
    print(f"=== {sig['name']} 信号 {sig['date']} ===")
    print(f"做多: {longs} | 做空: {shorts}")

    # 2. 风控门 (下单前)
    ok, reason = risk_gate()
    if not ok:
        print(f"⛔ 风控拦截: {reason} — 不执行调仓")
        append_ledger([{"event": "blocked", "reason": reason, "ts": _now_iso()}])
        return 0

    # 3. 当前持仓 + 订单 (敞口随风控乘数缩放: 事件/regime/波动率目标/疯牛保险)
    current = fetch_current_positions()
    per_leg = allocation_per_leg()
    long_mult = float(sig.get("long_mult", 1.0))
    short_mult = float(sig.get("short_mult", 1.0))
    orders = build_orders(
        longs, shorts, current,
        per_leg_long=per_leg * long_mult,
        per_leg_short=per_leg * short_mult,
    )
    if not orders:
        print("持仓已对齐, 无调仓")
        return 0

    print(f"\n每腿名义: 多 {per_leg * long_mult:.0f}U / 空 {per_leg * short_mult:.0f}U "
          f"(基准 {per_leg:.0f}U × long/short_mult {long_mult:.2f}/{short_mult:.2f})")
    for o in orders:
        print(f"  [{o['action']}] {o['symbol']:<20} {o['side']:<5} {o['notional']:.0f}U")

    # 4. 执行 (dry-run 只打印)
    results = execute_orders(orders, dry_run=args.dry_run)
    append_ledger(results)
    ok_n = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n完成: {ok_n}/{len(results)} 成功" + (" (dry-run 未下单)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
