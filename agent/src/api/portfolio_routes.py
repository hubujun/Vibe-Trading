"""Portfolio Studio HTTP routes — risk x-ray, constraints, optimizer, rebalance.

Mounted by ``agent/api_server.py`` via ``register_portfolio_routes(app)``.

- ``POST /api/portfolio/xray`` — risk x-ray for a weighted basket: send
  close-price series plus weights, get back concentration / volatility /
  drawdown / tail risk / diversification / correlation.
- ``POST /api/portfolio/rebalance-notes`` — per-date weight-change notes
  (turnover, entries, exits, top moves) for a target-position frame.
- ``POST /api/portfolio/constraints`` — apply a ``constraints`` spec
  (max_weight / min_weight / group_exposure) to a signed weight frame.
- ``POST /api/portfolio/optimize`` — run the turnover-aware optimizer on a
  returns panel plus a signal frame.
- ``POST /api/portfolio/rebalance-plan`` — read-only preview of the order
  diff from current positions to target weights, with the paper engine's
  risk gates applied (no execution).
- ``POST /api/portfolio/rebalance-execute`` — execute that diff through the
  paper engine (default) or the live executor (explicit ``execution="live"``
  plus ``confirm=true``; additionally requires a configured live executor).

All six are computations over caller-supplied data (no network, no
artifacts), so the same engine serves the agent tool, the CLI, and this API.
The heavy lifting lives in :mod:`backtest.risk_xray`, :mod:`backtest.constraints`,
:mod:`backtest.rebalance_notes`, and :mod:`backtest.optimizers.turnover_aware`;
these endpoints only adapt them to HTTP. Input errors surface as 400s.
"""

from __future__ import annotations

import sys as _sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

__all__ = [
    "XrayRequest",
    "RebalanceNotesRequest",
    "ConstraintsRequest",
    "OptimizeRequest",
    "RebalancePlanRequest",
    "RebalanceExecuteRequest",
    "register_portfolio_routes",
]

#: Autopilot runtime root — the same directory family the paper engine
#: persists its trade ledger under (``<agent>/runs/autopilot``).
_RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "runs" / "autopilot"


# ============================================================================
# Pydantic Models
# ============================================================================


class XrayRequest(BaseModel):
    """Body for ``POST /api/portfolio/xray``."""

    closes: Dict[str, List[float]] = Field(
        ..., description="symbol → close-price series (equal length)"
    )
    weights: Dict[str, float] = Field(
        ..., description="symbol → target weight; renormalized to 1.0, long-only"
    )
    dates: Optional[List[str]] = Field(
        None, description="Optional ISO dates aligned with every price series"
    )
    periods_per_year: int = Field(252, ge=1, description="Annualization factor")
    var_levels: List[float] = Field(
        [0.95, 0.99], description="Historical VaR / ES levels in (0, 1)"
    )
    min_history: int = Field(
        30, ge=1, description="Minimum valid bars a symbol needs to be included"
    )


class RebalanceNotesRequest(BaseModel):
    """Body for ``POST /api/portfolio/rebalance-notes``."""

    target_pos: Dict[str, Dict[str, float]] = Field(
        ..., description="date → symbol → target weight (NaN cells as zero)"
    )
    top_n: int = Field(5, ge=1, description="Largest per-name moves to keep per date")


class ConstraintsRequest(BaseModel):
    """Body for ``POST /api/portfolio/constraints``."""

    frame: Dict[str, Dict[str, float]] = Field(
        ..., description="date → symbol → signed weight (optimizer output)"
    )
    constraints: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Constraint specs: max_weight / min_weight / group_exposure",
    )


class OptimizeRequest(BaseModel):
    """Body for ``POST /api/portfolio/optimize`` (turnover-aware optimizer)."""

    returns: Dict[str, Dict[str, float]] = Field(
        ..., description="date → symbol → return (must pre-date each decision bar)"
    )
    positions: Dict[str, Dict[str, float]] = Field(
        ..., description="date → symbol → raw signal position (signed)"
    )
    lookback: int = Field(60, ge=5, description="Rolling covariance window")
    risk_aversion: float = Field(1.0, description="Weight on the variance term")
    turnover_penalty: float = Field(0.0, ge=0.0, description="L1 turnover penalty")
    max_per_name: Optional[float] = Field(None, description="Per-asset weight cap")
    groups: Dict[str, str] = Field(
        default_factory=dict, description="asset code → group name"
    )
    max_per_group: Dict[str, float] = Field(
        default_factory=dict, description="group → exposure cap"
    )


class RebalancePlanRequest(BaseModel):
    """Body for ``POST /api/portfolio/rebalance-plan`` (read-only preview)."""

    target_weights: Dict[str, float] = Field(
        ..., description="symbol → target weight; long-only, sums to 1.0"
    )
    current_positions: Dict[str, float] = Field(
        default_factory=dict,
        description="symbol → current market value in USD (absent = flat)",
    )
    portfolio_value: float = Field(
        ..., gt=0, description="Total portfolio value (USD) converting weights to notionals"
    )
    min_notional: float = Field(
        1.0, ge=0.0, description="Orders below this USD amount are skipped"
    )


class RebalanceExecuteRequest(RebalancePlanRequest):
    """Body for ``POST /api/portfolio/rebalance-execute``."""

    execution: str = Field(
        "paper", description="paper (default) or live — live requires confirm=true"
    )
    confirm: bool = Field(
        False, description="Acknowledge execution; mandatory for live"
    )
    note: Optional[str] = Field(
        None, description="Optional audit note returned with the plan"
    )


# ============================================================================
# Helpers
# ============================================================================


def _frame_from_mapping(mapping: Dict[str, Dict[str, float]], label: str) -> pd.DataFrame:
    """Build a date-keyed frame from ``{date: {symbol: value}}``.

    Raises:
        HTTPException: 400 when the mapping is empty or dates do not parse.
    """
    if not mapping:
        raise HTTPException(status_code=400, detail=f"{label} must not be empty")
    frame = pd.DataFrame.from_dict(mapping, orient="index")
    try:
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail=f"{label} dates must be parseable ISO dates: {exc}"
        ) from exc
    frame = frame.sort_index()
    if len(frame) < 2:
        raise HTTPException(
            status_code=400, detail=f"{label} needs at least 2 dated rows"
        )
    return frame


def _frame_to_dict(frame: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    """JSON-safe nested dict, dropping NaN cells and stringifying the index."""
    out: Dict[str, Dict[str, float]] = {}
    for dt, row in frame.iterrows():
        key = str(dt.date()) if hasattr(dt, "date") else str(dt)
        out[key] = {
            str(code): float(value)
            for code, value in row.items()
            if pd.notna(value)
        }
    return out


def _check_var_levels(levels: List[float]) -> None:
    for level in levels:
        if not 0.0 < level < 1.0:
            raise HTTPException(
                status_code=400,
                detail=f"var_levels must lie in (0, 1), got {level!r}",
            )


def _build_rebalance_orders(
    target_weights: Dict[str, float],
    current_positions: Dict[str, float],
    portfolio_value: float,
    min_notional: float = 1.0,
) -> List[Dict[str, Any]]:
    """Diff current notionals against target weights into an order list.

    Symbols held but absent from *target_weights* are fully closed; symbols
    targeted but not held are opened. Orders below *min_notional* are skipped.

    Raises:
        HTTPException: 400 when weights are empty, negative, or do not sum
            to ~1.0 (long-only target weights).
    """
    if not target_weights:
        raise HTTPException(status_code=400, detail="target_weights must not be empty")
    targets = {str(sym).strip().upper(): float(w) for sym, w in target_weights.items()}
    if any(w < 0 for w in targets.values()):
        raise HTTPException(
            status_code=400,
            detail="target_weights must be long-only (no negative weights)",
        )
    total = sum(targets.values())
    if abs(total - 1.0) > 1e-6:
        raise HTTPException(
            status_code=400,
            detail=f"target_weights must sum to 1.0, got {total:.6f}",
        )

    current = {
        str(sym).strip().upper(): float(value)
        for sym, value in current_positions.items()
    }
    orders: List[Dict[str, Any]] = []
    for symbol in sorted(set(targets) | set(current)):
        target = targets.get(symbol, 0.0) * portfolio_value
        cur = current.get(symbol, 0.0)
        delta = target - cur
        if abs(delta) < min_notional:
            continue
        orders.append(
            {
                "symbol": symbol,
                "side": "buy" if delta > 0 else "sell",
                "notional": round(abs(delta), 2),
                "current_notional": round(cur, 2),
                "target_notional": round(target, 2),
            }
        )
    return orders


def _apply_rebalance_gates(
    orders: List[Dict[str, Any]],
    *,
    max_order_notional_usd: float,
    max_total_exposure_usd: float,
    open_exposure_usd: float,
    max_trades_per_day: int,
    daily_order_count: int,
) -> Dict[str, List[Dict[str, Any]]]:
    """Split orders into executable vs blocked by the engine's own gates.

    Mirrors :meth:`PaperEngine.place_order` enforcement: per-order notional
    cap, daily buy quota, and aggregate exposure cap. Sells are never
    quota-limited (closing positions must not be blocked).
    """
    executable: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    buys = daily_order_count
    buy_notional = 0.0
    for order in orders:
        if order["notional"] > max_order_notional_usd:
            blocked.append(
                {
                    **order,
                    "reason": f"notional exceeds max_order_notional_usd={max_order_notional_usd:.2f}",
                }
            )
            continue
        if order["side"] == "buy":
            if buys >= max_trades_per_day:
                blocked.append({**order, "reason": "daily order limit reached"})
                continue
            if open_exposure_usd + buy_notional + order["notional"] > max_total_exposure_usd:
                blocked.append({**order, "reason": "exposure limit reached"})
                continue
            buys += 1
            buy_notional += order["notional"]
        executable.append(order)
    return {"orders": executable, "blocked": blocked}


def _plan_rebalance(payload: RebalancePlanRequest, engine: Any) -> Dict[str, Any]:
    """Build the diff orders + risk gates for a rebalance (never executes)."""
    orders = _build_rebalance_orders(
        payload.target_weights,
        payload.current_positions,
        payload.portfolio_value,
        payload.min_notional,
    )
    gates = _apply_rebalance_gates(
        orders,
        max_order_notional_usd=engine.config.max_order_notional_usd,
        max_total_exposure_usd=engine.config.max_total_exposure_usd,
        open_exposure_usd=engine.open_exposure_usd(),
        max_trades_per_day=engine.config.max_trades_per_day,
        daily_order_count=engine.orders_today(),
    )
    executable = gates["orders"]
    total = sum(order["notional"] for order in executable)
    buy = sum(order["notional"] for order in executable if order["side"] == "buy")
    return {
        "orders": executable,
        "blocked": gates["blocked"],
        "summary": {
            "order_count": len(executable),
            "blocked_count": len(gates["blocked"]),
            "total_notional": round(total, 2),
            "buy_notional": round(buy, 2),
        },
    }


# ============================================================================
# Route registration
# ============================================================================


def register_portfolio_routes(
    app: FastAPI,
    require_auth: Any | None = None,
    paper_engine_factory: Callable[[], Any] | None = None,
    live_executor_factory: Callable[[], Any] | None = None,
) -> None:
    """Mount the Portfolio Studio endpoints onto ``app``.

    Args:
        app: The FastAPI application to register routes on.
        require_auth: Auth dependency; when ``None`` it is resolved from the
            ``api_server`` module (mirroring ``register_options_lab_routes``).
        paper_engine_factory: Callable returning the paper engine used for
            plan risk gates and paper execution. Defaults to a
            :class:`PaperEngine` over the shared autopilot config.
        live_executor_factory: Callable returning the live executor used
            for ``execution="live"``. When ``None``, live execution is
            rejected with 503 — real funds require an explicit deployment
            choice plus ``confirm=true`` per request.
    """
    h = _sys.modules.get("api_server")
    if h is None:
        raise RuntimeError(
            "register_portfolio_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )
    if require_auth is None:
        require_auth = h.require_auth

    if paper_engine_factory is None:
        from src.crypto_autopilot.config import load_autopilot_config
        from src.crypto_autopilot.paper_engine import PaperEngine

        def default_paper_engine_factory() -> Any:
            return PaperEngine(
                config=load_autopilot_config(),
                runtime_root=_RUNTIME_ROOT,
            )

        paper_engine_factory = default_paper_engine_factory

    @app.post(
        "/api/portfolio/xray",
        response_model=Dict[str, Any],
        dependencies=[Depends(require_auth)],
    )
    async def portfolio_xray_endpoint(payload: XrayRequest) -> Dict[str, Any]:
        """Risk x-ray for a weighted basket of close-price series."""
        from backtest.risk_xray import compute_risk_xray

        _check_var_levels(payload.var_levels)
        closes = pd.DataFrame(payload.closes)
        if payload.dates is not None:
            if len(payload.dates) != len(closes):
                raise HTTPException(
                    status_code=400,
                    detail="dates length must match the length of each price series",
                )
            closes.index = pd.DatetimeIndex(pd.to_datetime(payload.dates))
        try:
            report = compute_risk_xray(
                closes,
                payload.weights,
                periods_per_year=payload.periods_per_year,
                var_levels=tuple(payload.var_levels),
                min_history=payload.min_history,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - unexpected engine failure
            raise HTTPException(status_code=502, detail=f"risk x-ray failed: {exc}") from exc
        return report

    @app.post(
        "/api/portfolio/rebalance-notes",
        response_model=Dict[str, Any],
        dependencies=[Depends(require_auth)],
    )
    async def portfolio_rebalance_notes_endpoint(
        payload: RebalanceNotesRequest,
    ) -> Dict[str, Any]:
        """Per-date weight-change notes for a target-position frame."""
        from backtest.rebalance_notes import compute_rebalance_notes

        try:
            frame = _frame_from_mapping(payload.target_pos, "target_pos")
            notes = compute_rebalance_notes(frame, top_n=payload.top_n)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - unexpected engine failure
            raise HTTPException(
                status_code=502, detail=f"rebalance notes failed: {exc}"
            ) from exc
        return notes

    @app.post(
        "/api/portfolio/constraints",
        response_model=Dict[str, Any],
        dependencies=[Depends(require_auth)],
    )
    async def portfolio_constraints_endpoint(
        payload: ConstraintsRequest,
    ) -> Dict[str, Any]:
        """Apply constraint specs to a signed weight frame and report the diff."""
        from backtest.constraints import apply_constraints_frame, load_constraints

        try:
            frame = _frame_from_mapping(payload.frame, "frame")
            constraints = load_constraints({"constraints": payload.constraints})
            adjusted = apply_constraints_frame(frame, constraints)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - unexpected engine failure
            raise HTTPException(
                status_code=502, detail=f"constraints application failed: {exc}"
            ) from exc

        before = frame.to_numpy(dtype=float)
        after = adjusted.to_numpy(dtype=float)
        adjusted_cells = int((before != after).sum())
        specs = []
        for spec in payload.constraints:
            if spec.get("type") == "max_weight":
                specs.append(f"max_weight cap {spec.get('cap')}")
            elif spec.get("type") == "min_weight":
                specs.append(f"min_weight floor {spec.get('floor')}")
            elif spec.get("type") == "group_exposure":
                specs.append("group_exposure")
            else:
                specs.append(f"{spec.get('type')}")
        return {
            "frame": _frame_to_dict(adjusted),
            "summary": {
                "dates": int(len(adjusted)),
                "assets": list(adjusted.columns),
                "constraints": specs,
                "adjusted_cells": adjusted_cells,
            },
        }

    @app.post(
        "/api/portfolio/optimize",
        response_model=Dict[str, Any],
        dependencies=[Depends(require_auth)],
    )
    async def portfolio_optimize_endpoint(
        payload: OptimizeRequest,
    ) -> Dict[str, Any]:
        """Turnover-aware optimization of a signal frame over a returns panel."""
        from backtest.optimizers.turnover_aware import TurnoverAwareOptimizer

        try:
            returns = _frame_from_mapping(payload.returns, "returns")
            positions = _frame_from_mapping(payload.positions, "positions")
            optimizer = TurnoverAwareOptimizer(
                lookback=payload.lookback,
                risk_aversion=payload.risk_aversion,
                turnover_penalty=payload.turnover_penalty,
                max_per_name=payload.max_per_name,
                groups=payload.groups,
                max_per_group=payload.max_per_group,
            )
            adjusted = optimizer.optimize(returns, positions, positions.index)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - unexpected engine failure
            raise HTTPException(
                status_code=502, detail=f"optimization failed: {exc}"
            ) from exc
        return {
            "frame": _frame_to_dict(adjusted),
            "summary": {
                "optimizer": "turnover_aware",
                "lookback": payload.lookback,
                "turnover_penalty": payload.turnover_penalty,
                "dates": int(len(adjusted)),
                "assets": list(adjusted.columns),
            },
        }

    @app.post(
        "/api/portfolio/rebalance-plan",
        response_model=Dict[str, Any],
        dependencies=[Depends(require_auth)],
    )
    async def portfolio_rebalance_plan_endpoint(
        payload: RebalancePlanRequest,
    ) -> Dict[str, Any]:
        """Preview the order diff from current positions to target weights.

        Read-only: applies the paper engine's risk gates (per-order notional
        cap, daily buy quota, exposure cap) and reports which orders would be
        blocked — nothing is placed.
        """
        try:
            engine = paper_engine_factory()
            plan = _plan_rebalance(payload, engine)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - unexpected engine failure
            raise HTTPException(
                status_code=502, detail=f"rebalance plan failed: {exc}"
            ) from exc
        return {"execution": "preview", **plan}

    @app.post(
        "/api/portfolio/rebalance-execute",
        response_model=Dict[str, Any],
        dependencies=[Depends(require_auth)],
    )
    async def portfolio_rebalance_execute_endpoint(
        payload: RebalanceExecuteRequest,
    ) -> Dict[str, Any]:
        """Execute a rebalance plan (paper by default; live is opt-in).

        The plan is always recomputed server-side from the target weights.
        Live execution requires ``execution="live"`` *and* ``confirm=true``
        *and* a configured live executor — otherwise no order reaches a real
        broker. Sells are exempt from the daily quota; blocked orders never
        touch an engine.
        """
        if payload.execution not in {"paper", "live"}:
            raise HTTPException(
                status_code=400,
                detail="execution must be 'paper' or 'live'",
            )
        if payload.execution == "live":
            if live_executor_factory is None:
                raise HTTPException(
                    status_code=503,
                    detail="live execution not configured on this server",
                )
            if not payload.confirm:
                raise HTTPException(
                    status_code=400,
                    detail="live execution requires confirm=true",
                )

        try:
            engine = paper_engine_factory()
            plan = _plan_rebalance(payload, engine)
            executor = (
                live_executor_factory() if payload.execution == "live" else engine
            )
            results: List[Dict[str, Any]] = []
            for order in plan["orders"]:
                try:
                    result = executor.place_order(
                        order["symbol"], order["side"], order["notional"],
                    )
                except Exception as exc:  # noqa: BLE001 - per-order isolation
                    result = {"status": "error", "error": str(exc)}
                outcome = {
                    "symbol": order["symbol"],
                    "side": order["side"],
                    "notional": order["notional"],
                    "status": result.get("status", "error"),
                }
                if result.get("error"):
                    outcome["detail"] = result["error"]
                results.append(outcome)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - unexpected engine failure
            raise HTTPException(
                status_code=502, detail=f"rebalance execution failed: {exc}"
            ) from exc

        return {
            "execution": payload.execution,
            "note": payload.note,
            "orders": results,
            "blocked": plan["blocked"],
            "summary": {
                "submitted": len(results),
                "ok": sum(1 for r in results if r["status"] == "ok"),
                "rejected": sum(1 for r in results if r["status"] == "rejected"),
                "failed": sum(1 for r in results if r["status"] == "error"),
                "blocked_count": len(plan["blocked"]),
            },
        }
