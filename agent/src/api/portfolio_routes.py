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

All four are pure computations over caller-supplied data (no network, no
artifacts), so the same engine serves the agent tool, the CLI, and this API.
The heavy lifting lives in :mod:`backtest.risk_xray`, :mod:`backtest.constraints`,
:mod:`backtest.rebalance_notes`, and :mod:`backtest.optimizers.turnover_aware`;
these endpoints only adapt them to HTTP. Input errors surface as 400s.
"""

from __future__ import annotations

import sys as _sys
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

__all__ = [
    "XrayRequest",
    "RebalanceNotesRequest",
    "ConstraintsRequest",
    "OptimizeRequest",
    "register_portfolio_routes",
]


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


# ============================================================================
# Route registration
# ============================================================================


def register_portfolio_routes(
    app: FastAPI,
    require_auth: Any | None = None,
) -> None:
    """Mount the Portfolio Studio endpoints onto ``app``.

    Args:
        app: The FastAPI application to register routes on.
        require_auth: Auth dependency; when ``None`` it is resolved from the
            ``api_server`` module (mirroring ``register_options_lab_routes``).
    """
    h = _sys.modules.get("api_server")
    if h is None:
        raise RuntimeError(
            "register_portfolio_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )
    if require_auth is None:
        require_auth = h.require_auth

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
