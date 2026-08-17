"""Strategy lifecycle workbench routes — 策略级生命周期流水线聚合视图.

方案C: 把 Combo(研究/模拟) 与 Autopilot(执行) 统一为一条策略生命周期流水线.

- ``GET  /api/workbench`` — 聚合视图: 策略级状态机 + combo 研究数据 +
  autopilot 执行数据, 一屏看全 research → paper → live → review.
- ``POST /api/workbench/strategies/{sid}/transition`` — 推进/回退/暂停/恢复
  策略生命周期 (仅更新策略级状态机文件, 不触碰 autopilot broker —
  执行层启停仍在 CLI, kill switch 仍在 ``/live/halt``).

策略状态机:
    research → paper → live  (paper/live 可进入 paused, 恢复回原阶段)
    back_to_research 从任意阶段软重置回研究.

持久化: ``~/.vibe-trading/workbench/strategies.json`` (fail-open:
文件缺失/损坏时按默认策略 + 推断阶段返回, 写入失败不炸接口).
"""

from __future__ import annotations

import json
import logging
import sys as _sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.api.autopilot_routes import (
    AutopilotStatusResponse,
    _load_counter,
    _load_data_health,
    _load_halt,
    _load_health,
    _load_pipeline_state,
)
from src.api.combo_routes import ComboSummary, _load_hypotheses, _load_metrics, _load_paper, _load_signal

logger = logging.getLogger(__name__)

__all__ = [
    "WorkbenchStrategy",
    "WorkbenchResponse",
    "TransitionRequest",
    "register_workbench_routes",
]

#: 策略级状态机持久化位置 (home 目录下, 不受 agent/runs 迁移影响).
_WORKBENCH_ROOT = Path.home() / ".vibe-trading" / "workbench"
_STRATEGIES_PATH = _WORKBENCH_ROOT / "strategies.json"

#: combo paper 运行时根 — 用于推断初始阶段 (已有模拟盘 → paper).
_COMBO_RUNTIME_ROOT = Path.home() / ".vibe-trading" / "runs" / "paper_combo"

#: 合法阶段集合 (生命周期流水线节点).
PHASES = ["research", "paper", "live", "review"]

#: 合法迁移动作 → 允许的来源阶段集合.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "start_paper": {"research"},          # 研究 → 模拟
    "promote_live": {"paper"},            # 模拟 → 执行
    "pause": {"paper", "live"},           # 挂起
    "resume": {"paused"},                 # 恢复 (回到 paused_from)
    "back_to_research": set(PHASES),      # 软重置
}

#: 默认策略种子 — 当前 fork 的主策略 (BAB+high52w 双因子).
DEFAULT_STRATEGIES: list[dict[str, Any]] = [
    {
        "strategy_id": "combo_bab_52w",
        "name": "BAB+high52w 双因子组合",
        "description": "低波动异象 × 52周高点动量 · 等权横截面 · 多 top3 空 bottom3 · 单边成本 0.1%",
        "factors": ["BAB", "high52w"],
        "weights": {"BAB": 0.5, "high52w": 0.5},
        "universe_size": 10,
        "rebalance": "日频 · 每日 07:00",
    },
]

#: 内存写锁 — 状态机文件写操作串行化.
_lock = threading.Lock()


# ============================================================================
# Pydantic Models
# ============================================================================


class WorkbenchPhaseEvent(BaseModel):
    """一次生命周期迁移的记录."""

    phase: str
    at: str
    action: str
    note: Optional[str] = None


class WorkbenchStrategy(BaseModel):
    """一条策略的生命周期状态 + 定义."""

    strategy_id: str
    name: str
    description: str = ""
    factors: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    universe_size: int = 0
    rebalance: str = ""
    phase: str = "research"
    paused_from: Optional[str] = None
    phase_history: list[WorkbenchPhaseEvent] = Field(default_factory=list)
    updated_at: Optional[str] = None


class WorkbenchResponse(BaseModel):
    """聚合视图: 策略状态机 + 研究数据 + 执行数据."""

    strategies: list[WorkbenchStrategy] = Field(default_factory=list)
    combo: ComboSummary = Field(default_factory=ComboSummary)
    autopilot: AutopilotStatusResponse | None = None
    updated_at: Optional[str] = None


class TransitionRequest(BaseModel):
    """生命周期迁移请求体."""

    action: str = Field(..., description="start_paper | promote_live | pause | resume | back_to_research")
    note: Optional[str] = None


# ============================================================================
# 状态机持久化 (fail-open)
# ============================================================================


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_strategies() -> list[dict[str, Any]]:
    """读取持久化策略列表; 缺失/损坏时返回默认种子. 只读不改文件."""
    try:
        raw = json.loads(_STRATEGIES_PATH.read_text(encoding="utf-8"))
        strategies = raw.get("strategies", [])
        if isinstance(strategies, list) and strategies:
            return strategies
    except (OSError, ValueError, TypeError):
        pass
    return _seed_strategies()


def _seed_strategies() -> list[dict[str, Any]]:
    """返回默认策略种子, 并根据 combo 模拟盘是否已启动推断初始阶段.

    paper_combo/state.json 存在 started_at → 模拟盘已在跑 → phase=paper,
    否则 research.
    """
    phase = "research"
    try:
        state = json.loads((_COMBO_RUNTIME_ROOT / "state.json").read_text(encoding="utf-8"))
        if state.get("started_at"):
            phase = "paper"
    except (OSError, ValueError, TypeError):
        pass
    seeds = []
    for s in DEFAULT_STRATEGIES:
        seeds.append({**s, "phase": phase, "updated_at": _now_iso()})
    return seeds


def _write_strategies(strategies: list[dict[str, Any]]) -> None:
    """原子写策略列表 (tmp + rename)."""
    _WORKBENCH_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _STRATEGIES_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"strategies": strategies}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(_STRATEGIES_PATH)


def _load_strategy(strategy_id: str) -> dict[str, Any] | None:
    return next((s for s in _read_strategies() if s.get("strategy_id") == strategy_id), None)


# ============================================================================
# 生命周期迁移
# ============================================================================


def _apply_transition(strategy: dict[str, Any], action: str, note: Optional[str]) -> dict[str, Any]:
    """在内存中执行一次合法迁移; 非法动作抛 ValueError."""
    current = strategy.get("phase", "research")
    allowed = ALLOWED_TRANSITIONS.get(action)
    if allowed is None:
        raise ValueError(f"未知动作: {action}")
    if current == "paused" and action != "resume" and action != "back_to_research":
        raise ValueError(f"策略处于 paused, 请先 resume 或 back_to_research")

    if action == "start_paper" and current == "research":
        next_phase = "paper"
    elif action == "promote_live" and current == "paper":
        next_phase = "live"
    elif action == "pause" and current in ("paper", "live"):
        next_phase = "paused"
    elif action == "resume" and current == "paused":
        next_phase = strategy.get("paused_from") or "paper"
    elif action == "back_to_research":
        next_phase = "research"
    else:
        raise ValueError(f"非法迁移: {action} 不允许从 {current} 阶段")

    if current not in allowed:
        raise ValueError(f"非法迁移: {action} 不允许从 {current} 阶段")

    history = list(strategy.get("phase_history", []))
    history.append(
        {
            "phase": next_phase,
            "at": _now_iso(),
            "action": action,
            "note": note,
        }
    )
    strategy["phase"] = next_phase
    strategy["paused_from"] = current if next_phase == "paused" else None
    strategy["phase_history"] = history[-50:]  # 只保留最近 50 条
    strategy["updated_at"] = _now_iso()
    return strategy


def _transition(strategy_id: str, action: str, note: Optional[str]) -> WorkbenchStrategy:
    """加载 → 校验 → 迁移 → 持久化 (串行)."""
    with _lock:
        strategies = _read_strategies()
        strategy = next((s for s in strategies if s.get("strategy_id") == strategy_id), None)
        if strategy is None:
            raise HTTPException(status_code=404, detail=f"策略不存在: {strategy_id}")
        try:
            _apply_transition(strategy, action, note)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            _write_strategies(strategies)
        except OSError as exc:  # 持久化失败不吞 — 返回 500 让前端感知
            logger.warning("workbench persist failed: %s", exc)
            raise HTTPException(status_code=500, detail="状态机持久化失败") from exc
        return WorkbenchStrategy(**strategy)


# ============================================================================
# 聚合加载器
# ============================================================================


def _load_autopilot_status() -> AutopilotStatusResponse:
    """聚合 autopilot 只读状态 (复用 autopilot_routes 的 loader)."""
    from src.api.autopilot_routes import AutopilotConfigSummary
    from src.crypto_autopilot.config import load_autopilot_config

    config = load_autopilot_config()
    return AutopilotStatusResponse(
        pipeline=_load_pipeline_state(),
        health=_load_health(),
        halt=_load_halt(),
        counter=_load_counter(),
        config=AutopilotConfigSummary(
            enabled=config.enabled,
            pairs=list(config.pairs),
            max_order_notional_usd=config.max_order_notional_usd,
            max_total_exposure_usd=config.max_total_exposure_usd,
            max_trades_per_day=config.max_trades_per_day,
            mine_interval_hours=config.mine_interval_hours,
            evaluate_interval_hours=config.evaluate_interval_hours,
            trade_interval_minutes=config.trade_interval_minutes,
            feedback_interval_hours=config.feedback_interval_hours,
        ),
        data_health=_load_data_health(),
    )


# ============================================================================
# Registration
# ============================================================================

AuthDep = Any


def register_workbench_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount the workbench routes onto ``app``."""

    h = _sys.modules.get("api_server")
    if h is None:
        raise RuntimeError(
            "register_workbench_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )
    if require_auth is None:
        require_auth = h.require_auth

    @app.get(
        "/api/workbench",
        response_model=WorkbenchResponse,
        dependencies=[Depends(require_auth)],
    )
    async def workbench_summary() -> WorkbenchResponse:
        """聚合视图: 策略状态机 + combo 研究/模拟 + autopilot 执行."""
        strategies = [WorkbenchStrategy(**s) for s in _read_strategies()]
        try:
            autopilot = _load_autopilot_status()
        except Exception:  # noqa: BLE001 — 执行层不可用不拖垮工作台
            logger.warning("workbench: autopilot aggregation failed", exc_info=True)
            autopilot = None
        try:
            combo = ComboSummary(
                signal=_load_signal(),
                paper=_load_paper(),
                metrics=_load_metrics(),
                hypotheses=_load_hypotheses(),
            )
        except Exception:  # noqa: BLE001
            logger.warning("workbench: combo aggregation failed", exc_info=True)
            combo = ComboSummary()
        return WorkbenchResponse(
            strategies=strategies,
            combo=combo,
            autopilot=autopilot,
            updated_at=_now_iso(),
        )

    @app.post(
        "/api/workbench/strategies/{strategy_id}/transition",
        response_model=WorkbenchStrategy,
        dependencies=[Depends(require_auth)],
    )
    async def workbench_transition(
        strategy_id: str,
        body: TransitionRequest,
    ) -> WorkbenchStrategy:
        """推进/回退/暂停/恢复一条策略的生命周期."""
        return _transition(strategy_id, body.action, body.note)
