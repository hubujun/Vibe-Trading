"""老胡交易规则引擎 (2026-08-30, 实盘就绪第 1 步).

把老胡的 5 条交易纪律落成纯函数规则, 供执行层 (live_executor tick /
orchestrator tick_trade) 在每次交易前评估:

1. 周四五谨慎 — 周四五禁止开新多头 (可做空/平仓)
2. 宏观事件静默 — 事件时间 ±5 分钟禁止开新仓 (事件需带 time 字段)
3. 23:00/23:55 强制平仓 — 到点触发全部平仓
4. 连续 3 笔亏损当日停 — 今日平仓记录连续亏损 >= 3 笔, 当日不再开新仓
5. 日内亏损熔断 — 权益较日初基线跌幅 >= kill_loss_pct% → 熔断 (halt)

状态 (equity 基线/连亏计数) 持久化到 ~/.vibe-trading/autopilot/rule_state.json,
跨 tick 保留, 跨自然日自动重置.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_STATE_PATH = Path.home() / ".vibe-trading" / "autopilot" / "rule_state.json"
DEFAULT_TRADE_TZ = "Asia/Shanghai"


@dataclass
class RuleConfig:
    """规则参数 (来自 autopilot config 或默认值)."""

    cautious_weekdays: tuple[int, ...] = (3, 4)       # 周四(3)/周五(4)
    force_close_times: tuple[str, ...] = ("23:00", "23:55")
    macro_silence_minutes: int = 5
    max_consecutive_losses: int = 3
    daily_loss_pct: float = 5.0                        # 与 kill_loss_pct 对齐
    trade_tz: str = DEFAULT_TRADE_TZ


@dataclass
class RuleState:
    """跨 tick 状态 (按交易日持久化)."""

    day: str = ""                       # 状态所属日期 YYYY-MM-DD (交易时区)
    equity_baseline: float | None = None
    consecutive_losses: int = 0
    halted_today: bool = False
    force_closed: bool = False

    @classmethod
    def load(cls, path: Path = DEFAULT_STATE_PATH) -> "RuleState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path = DEFAULT_STATE_PATH) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass


@dataclass
class RuleVerdict:
    """一次评估的结论 (执行层据此决定动作)."""

    can_trade: bool = True
    reason: str | None = None            # 禁止/动作原因 (白话, 审计用)
    blocked_side: str | None = None      # "long" | "short" | None (周四五限制多头)
    action: str | None = None            # "force_close" | "halt" | None
    rules: list[dict] = field(default_factory=list)  # 每条规则明细


def _now_in_tz(trade_tz: str) -> datetime:
    return datetime.now(ZoneInfo(trade_tz))


def _is_weekday_cautious(dt: datetime, cfg: RuleConfig) -> bool:
    return dt.weekday() in cfg.cautious_weekdays


def _macro_silence_window(events: list[dict] | None, dt: datetime, cfg: RuleConfig) -> str | None:
    """事件时间 ±silence 分钟内的静默窗口. 事件需带 time 字段 (HH:MM), 无时间跳过."""
    for e in events or []:
        et = (e or {}).get("time")
        if not et or not e.get("date"):
            continue
        try:
            event_dt = datetime.fromisoformat(f"{e['date']}T{et}:00").replace(tzinfo=dt.tzinfo)
        except ValueError:
            continue
        if abs((dt - event_dt).total_seconds()) <= cfg.macro_silence_minutes * 60:
            return f"宏观事件静默: {e.get('title', '')[:30]} ({e['date']} {et})"
    return None


def _force_close_due(dt: datetime, cfg: RuleConfig) -> str | None:
    hm = dt.strftime("%H:%M")
    for t in cfg.force_close_times:
        if hm >= t:  # 到点或过点 (含 23:00~23:59 区间)
            return f"强制平仓时间 {t} 已到"
    return None


def evaluate(
    now: datetime | None = None,
    *,
    state: RuleState | None = None,
    equity_now: float | None = None,
    closed_trades_today: list[dict] | None = None,
    events: list[dict] | None = None,
    cfg: RuleConfig | None = None,
) -> RuleVerdict:
    """评估全部规则, 返回 verdict. 纯函数 + 状态入参 (状态持久化由调用方负责).

    Args:
        now: 当前时间 (交易时区); None 用系统时间
        state: 跨 tick 状态 (含 equity 基线/连亏计数); None 用默认
        equity_now: 当前账户权益 (无则跳过日内熔断)
        closed_trades_today: 今日已平仓记录 (连续亏损判断)
        events: 宏观事件列表 (静默判断)
        cfg: 规则参数
    """
    cfg = cfg or RuleConfig()
    tz = ZoneInfo(cfg.trade_tz)
    dt = (now or datetime.now(tz))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    st = state or RuleState()
    verdict = RuleVerdict()

    # 1. 周四五谨慎 (只限新多头)
    if _is_weekday_cautious(dt, cfg):
        verdict.blocked_side = "long"
        verdict.rules.append({
            "rule": "weekday_cautious", "blocked": False,
            "detail": f"{dt.strftime('%A')} 谨慎日 — 禁止开新多头",
        })

    # 2. 宏观事件静默
    silence = _macro_silence_window(events, dt, cfg)
    if silence:
        verdict.can_trade = False
        verdict.reason = silence
        verdict.action = None
        verdict.rules.append({"rule": "macro_silence", "blocked": True, "detail": silence})

    # 3. 强制平仓
    fc = _force_close_due(dt, cfg)
    if fc:
        verdict.action = "force_close"
        verdict.can_trade = False
        verdict.reason = verdict.reason or fc
        verdict.rules.append({"rule": "force_close", "blocked": True, "detail": fc})

    # 4. 连续亏损当日停
    losses = 0
    for t in (closed_trades_today or []):
        if t.get("pnl", 0) < 0 or t.get("realized_pnl", 0) < 0:
            losses += 1
        else:
            break  # 只数连续亏损
    if losses >= cfg.max_consecutive_losses:
        verdict.can_trade = False
        verdict.reason = verdict.reason or f"连续 {losses} 笔亏损, 当日停止交易"
        verdict.rules.append({
            "rule": "consecutive_losses", "blocked": True,
            "detail": f"连续亏损 {losses} 笔 >= {cfg.max_consecutive_losses}",
        })

    # 5. 日内亏损熔断
    if equity_now is not None and st.equity_baseline and st.equity_baseline > 0:
        loss_pct = (st.equity_baseline - equity_now) / st.equity_baseline * 100
        if loss_pct >= cfg.daily_loss_pct:
            verdict.can_trade = False
            verdict.action = "halt"
            verdict.reason = verdict.reason or (
                f"日内亏损 {loss_pct:.1f}% >= {cfg.daily_loss_pct}% 熔断"
            )
            verdict.rules.append({
                "rule": "daily_loss", "blocked": True, "action": "halt",
                "detail": f"权益 {equity_now:.2f} vs 基线 {st.equity_baseline:.2f} "
                          f"({loss_pct:.1f}%)",
            })

    if verdict.can_trade and not verdict.reason and not verdict.action:
        verdict.rules.append({"rule": "all_pass", "blocked": False, "detail": "全部规则通过"})
    return verdict
