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
        "top_n": 3,
        "bot_n": 3,
        "universe_size": 15,
        "rebalance": "日频 · 每日 07:00",
        "signal_definition": "combo_variant: weights={BAB:0.5,high52w:0.5} top_n=3 bot_n=3",
        "run_dir": str(_COMBO_RUNTIME_ROOT),
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
    top_n: int = 3
    bot_n: int = 3
    universe_size: int = 0
    rebalance: str = ""
    #: 可解析的信号定义 (daily_signal 按此生成信号) + 独立运行目录
    signal_definition: str = ""
    run_dir: str = ""
    phase: str = "research"
    paused_from: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    adaptation_history: list[dict[str, Any]] = Field(default_factory=list)
    phase_history: list[WorkbenchPhaseEvent] = Field(default_factory=list)
    updated_at: Optional[str] = None
    #: 运行时数据 (GET 聚合时填充): 模拟盘摘要 + 复盘输出 + 该策略自己的回测指标
    paper: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)
    strategy_backtest: dict[str, Any] = Field(default_factory=dict)


class WorkbenchResponse(BaseModel):
    """聚合视图: 策略状态机 + 研究数据 + 执行数据 + 复盘反馈."""

    strategies: list[WorkbenchStrategy] = Field(default_factory=list)
    combo: ComboSummary = Field(default_factory=ComboSummary)
    autopilot: AutopilotStatusResponse | None = None
    autopilot_trades: list[dict[str, Any]] = Field(default_factory=list)
    autopilot_positions: list[dict[str, Any]] = Field(default_factory=list)
    autopilot_performance: Optional[dict[str, Any]] = None
    autopilot_factors: Optional[dict[str, Any]] = None
    autopilot_factor_stats: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[str] = None


class TransitionRequest(BaseModel):
    """生命周期迁移请求体."""

    action: str = Field(..., description="start_paper | promote_live | pause | resume | back_to_research")
    note: Optional[str] = None


class SeedStrategyRequest(BaseModel):
    """从组合层变体播种新策略 (多策略并行入口)."""

    signal_definition: str = Field(..., description="combo_variant: ... 可解析的信号定义")
    name: Optional[str] = None
    description: Optional[str] = None


# ============================================================================
# 状态机持久化 (fail-open)
# ============================================================================


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_strategies() -> list[dict[str, Any]]:
    """读取持久化策略列表; 缺失/损坏时返回默认种子. 只读不改文件.

    旧记录 (无 signal_definition/run_dir) 按默认主策略字段迁移补齐.
    """
    defaults = DEFAULT_STRATEGIES[0]
    try:
        raw = json.loads(_STRATEGIES_PATH.read_text(encoding="utf-8"))
        strategies = raw.get("strategies", [])
        if isinstance(strategies, list) and strategies:
            migrated = []
            for s in strategies:
                s = dict(s)
                if not s.get("signal_definition"):
                    s["signal_definition"] = defaults["signal_definition"]
                if not s.get("run_dir"):
                    s["run_dir"] = defaults["run_dir"]
                s.setdefault("top_n", defaults["top_n"])
                s.setdefault("bot_n", defaults["bot_n"])
                migrated.append(s)
            return migrated
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


def _apply_adaptations(review: Any, strategies: list[dict[str, Any]]) -> dict[str, Any]:
    """应用复盘输出到策略参数 (第三圈).

    对每条策略: 用当前 params 计算自适应变更, 应用并记录到 adaptation_history.
    幂等: 无变更时原样返回. 持久化失败不阻断 (下次 GET 重试).
    """
    from src.strategy.review_engine import compute_adaptations

    adaptations: list[dict[str, Any]] = []
    with _lock:
        changed = False
        for strategy in strategies:
            params = dict(strategy.get("params") or {})
            params.setdefault("exposure_multiplier", 1.0)
            computed = compute_adaptations(review, params)
            if not computed:
                continue
            for a in computed:
                params[a.param] = a.to_value
                adaptations.append(a.to_dict())
                history = list(strategy.get("adaptation_history", []))
                history.append(
                    {
                        "param": a.param,
                        "from_value": a.from_value,
                        "to_value": a.to_value,
                        "reason": a.reason,
                        "at": a.at,
                    }
                )
                strategy["adaptation_history"] = history[-50:]
            strategy["params"] = params
            strategy["updated_at"] = _now_iso()
            changed = True
        if changed:
            try:
                _write_strategies(strategies)
            except OSError as exc:
                logger.warning("workbench: adaptation persist failed: %s", exc)
    return {"strategies": strategies, "adaptations": adaptations}


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
        """聚合视图: 策略状态机 + combo 研究/模拟 + autopilot 执行 + 复盘反馈."""
        raw_strategies = _read_strategies()
        try:
            autopilot = _load_autopilot_status()
        except Exception:  # noqa: BLE001 — 执行层不可用不拖垮工作台
            logger.warning("workbench: autopilot aggregation failed", exc_info=True)
            autopilot = None
        try:
            from src.api.autopilot_routes import (
                load_factor_stats_for_dashboard,
                load_factors_for_dashboard,
                load_performance_for_dashboard,
                load_positions_for_dashboard,
                load_trades_for_dashboard,
            )

            autopilot_trades = load_trades_for_dashboard(limit=30)
            autopilot_positions = load_positions_for_dashboard()
            autopilot_performance = load_performance_for_dashboard()
            autopilot_factors = load_factors_for_dashboard()
            autopilot_factor_stats = load_factor_stats_for_dashboard() or {}
        except Exception:  # noqa: BLE001
            logger.warning("workbench: autopilot detail aggregation failed", exc_info=True)
            autopilot_trades, autopilot_positions = [], []
            autopilot_performance = autopilot_factors = None
            autopilot_factor_stats = {}
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
        try:
            # Loop Engineering: 每策略独立复盘 (体检 + 假设流转 + 自适应)
            from src.strategy.review_engine import compute_review

            from src.hypotheses.registry import HypothesisRegistry
            from src.strategy.variant_backtester import load_backtest_cache
            from src.strategy.variant_generator import generate_variants

            hypotheses_path = Path.home() / ".vibe-trading" / "hypotheses.json"
            registry = HypothesisRegistry(hypotheses_path)
            backtest_cache = load_backtest_cache()
            # 第二圈: 变体生成 (全局, 供组合层)
            variants = generate_variants(registry, max_new=2)
            variant_metrics: dict[str, dict[str, Any]] = {}
            for h in registry.list():
                sd = str(getattr(h, "signal_definition", "") or "")
                if sd in backtest_cache:
                    m = backtest_cache[sd]
                    variant_metrics[str(h.hypothesis_id)] = {
                        k: m.get(k) for k in ("annual", "sharpe", "max_dd", "cum", "backtested_at")
                    }

            # per-strategy: 模拟盘摘要 + 复盘 + 参数自适应
            global_hypothesis_updates: list[dict[str, Any]] = []
            for s in raw_strategies:
                run_dir = Path(s.get("run_dir") or _COMBO_RUNTIME_ROOT)
                # 模拟盘摘要
                paper: dict[str, Any] = {}
                try:
                    st = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
                    paper = {
                        "nav": st.get("nav"),
                        "started_at": st.get("started_at"),
                        "last_signal_date": st.get("last_signal_date"),
                        "longs": st.get("last_longs", []),
                        "shorts": st.get("last_shorts", []),
                        "scores": st.get("scores", {}),
                        "trades": (st.get("trades") or [])[-5:],
                    }
                except (OSError, ValueError, TypeError):
                    pass
                s["paper"] = paper
                # 复盘: 基准备用该策略自己的回测缓存 (变体回测结果), 无则回退 COMBO2
                baseline = None
                sd = s.get("signal_definition") or ""
                if sd in backtest_cache:
                    m = backtest_cache[sd]
                    baseline = {"annual": m.get("annual"), "max_dd": m.get("max_dd")}
                    s["strategy_backtest"] = {k: m.get(k) for k in ("annual", "sharpe", "max_dd", "cum")}
                elif "_BASE_" in backtest_cache:
                    # 基策略自身: 用当前 universe 动态基准 (variant_backtester 产出)
                    m = backtest_cache["_BASE_"]
                    baseline = {"annual": m.get("annual"), "max_dd": m.get("max_dd")}
                    s["strategy_backtest"] = {k: m.get(k) for k in ("annual", "sharpe", "max_dd", "cum")}
                else:
                    s["strategy_backtest"] = {}
                try:
                    r = compute_review(
                        run_dir / "state.json",
                        _COMBO_RUNTIME_ROOT / "backtest_metrics.json",
                        hypotheses_path=hypotheses_path,
                        baseline=baseline,
                    )
                    # 第三圈: 参数自适应 — 应用到该策略自身
                    applied = _apply_adaptations(r, [s])
                    r.adaptations.extend(applied["adaptations"])
                    review_dict = r.to_dict()
                    # 假设流转记录只在基策略上收 (幂等, 避免重复)
                    if not global_hypothesis_updates:
                        global_hypothesis_updates = review_dict.get("hypothesis_updates", [])
                    review_dict.pop("hypothesis_updates", None)
                    review_dict.pop("variants", None)
                    review_dict.pop("variant_metrics", None)
                    s["review"] = review_dict
                except Exception:  # noqa: BLE001
                    logger.warning("workbench: review failed for %s", s.get("strategy_id"), exc_info=True)
                    s["review"] = {}
        except Exception:  # noqa: BLE001
            logger.warning("workbench: loop aggregation failed", exc_info=True)
            variants, variant_metrics, global_hypothesis_updates = [], {}, []

        review_dict = {
            "hypothesis_updates": global_hypothesis_updates,
            "variants": variants,
            "variant_metrics": variant_metrics,
            "reviewed_at": _now_iso(),
        }
        return WorkbenchResponse(
            strategies=[WorkbenchStrategy(**s) for s in raw_strategies],
            combo=combo,
            autopilot=autopilot,
            autopilot_trades=autopilot_trades,
            autopilot_positions=autopilot_positions,
            autopilot_performance=autopilot_performance,
            autopilot_factors=autopilot_factors,
            autopilot_factor_stats=autopilot_factor_stats,
            review=review_dict,
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

    @app.delete(
        "/api/workbench/strategies/{strategy_id}",
        dependencies=[Depends(require_auth)],
    )
    async def workbench_delete_strategy(strategy_id: str) -> dict[str, str]:
        """删除一条策略 (组合层变体播种的并行策略可清理)."""
        import shutil

        with _lock:
            strategies = _read_strategies()
            idx = next(
                (i for i, s in enumerate(strategies) if s.get("strategy_id") == strategy_id),
                None,
            )
            if idx is None:
                raise HTTPException(status_code=404, detail=f"策略不存在: {strategy_id}")
            removed = strategies.pop(idx)
            # 保护: 默认种子策略不可删 (删了下次 GET 会重新播种)
            if removed.get("strategy_id") == DEFAULT_STRATEGIES[0]["strategy_id"]:
                raise HTTPException(status_code=400, detail="基策略 (默认种子) 不可删除")
            _write_strategies(strategies)
        # 清理独立运行目录 (尽力而为, 不阻塞)
        run_dir = Path(removed.get("run_dir") or "")
        if run_dir.exists() and run_dir.name.startswith("paper_combo_"):
            shutil.rmtree(run_dir, ignore_errors=True)
        return {"deleted": strategy_id}

    @app.post(
        "/api/workbench/strategies",
        response_model=WorkbenchStrategy,
        dependencies=[Depends(require_auth)],
    )
    async def workbench_seed_strategy(body: SeedStrategyRequest) -> WorkbenchStrategy:
        """播种一条新策略 — 从组合层变体 (signal_definition) 创建并行走模拟盘.

        多策略并行入口: 每个变体有独立的状态机 + 运行目录 + 复盘基准.
        """
        import hashlib

        from src.strategy.variant_backtester import parse_signal_definition

        spec = parse_signal_definition(body.signal_definition)
        if spec is None:
            raise HTTPException(status_code=400, detail="signal_definition 无法解析")
        factors: list[str] = list(spec["factors"])
        weights: dict[str, float] = {k: float(v) for k, v in spec["weights"].items()}
        top_n: int = int(spec["top_n"])
        bot_n: int = int(spec["bot_n"])
        strategy_id = "combo_" + hashlib.md5(body.signal_definition.encode()).hexdigest()[:8]

        with _lock:
            strategies = _read_strategies()
            if any(s.get("strategy_id") == strategy_id for s in strategies):
                raise HTTPException(status_code=409, detail=f"策略已存在: {strategy_id}")
            run_dir = Path.home() / ".vibe-trading" / "runs" / f"paper_{strategy_id}"
            run_dir.mkdir(parents=True, exist_ok=True)
            state_path = run_dir / "state.json"
            if not state_path.exists():
                state_path.write_text(
                    json.dumps(
                        {
                            "started_at": None,
                            "nav": 1.0,
                            "last_signal_date": None,
                            "last_longs": [],
                            "last_shorts": [],
                            "trades": [],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            name = body.name or f"变体策略 ({strategy_id})"
            strategy = {
                "strategy_id": strategy_id,
                "name": name,
                "description": body.description or f"组合变体 · 因子 {', '.join(factors)} · 多 top{top_n} 空 bottom{bot_n}",
                "factors": list(factors),
                "weights": {k: round(float(v), 4) for k, v in weights.items()},
                "top_n": top_n,
                "bot_n": bot_n,
                "universe_size": 15,
                "rebalance": "日频 · 每日 07:00",
                "signal_definition": body.signal_definition,
                "run_dir": str(run_dir),
                "phase": "research",
                "params": {"exposure_multiplier": 1.0},
                "adaptation_history": [],
                "phase_history": [
                    {
                        "phase": "research",
                        "at": _now_iso(),
                        "action": "seeded",
                        "note": "从组合层变体播种",
                    }
                ],
                "updated_at": _now_iso(),
            }
            strategies.append(strategy)
            _write_strategies(strategies)
        return WorkbenchStrategy(**strategy)
