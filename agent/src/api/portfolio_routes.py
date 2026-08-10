"""Authenticated Web API for the local read-only portfolio dashboard plus
Portfolio Studio computation endpoints.

Read-only dashboard (upstream):
  GET  /api/portfolio                    latest snapshot
  POST /api/portfolio/refresh            refresh all enabled sources
  GET  /api/portfolio/refresh-status     live refresh progress
  POST /api/portfolio/sources/{id}/reconnect
  GET/PUT /api/portfolio/settings        display currency + source selection
  GET  /api/portfolio/history            complete snapshots
  GET  /api/portfolio/analysis-context   sanitized context for the agent
  GET  /api/portfolio/export.csv         CSV export

Portfolio Studio (local — pure computations over caller-supplied data):
  POST /api/portfolio/xray               risk x-ray of a weighted basket
  POST /api/portfolio/rebalance-notes    per-date weight-change notes
  POST /api/portfolio/constraints        apply max/min/group constraints
  POST /api/portfolio/optimize           turnover-aware optimizer

The heavy lifting lives in :mod:`src.portfolio.service`,
:mod:`backtest.risk_xray`, :mod:`backtest.constraints`,
:mod:`backtest.rebalance_notes` and :mod:`backtest.optimizers.turnover_aware`;
these endpoints only adapt them to HTTP. Input errors surface as 400s.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field

from src.api.security import require_auth, require_settings_write_auth
from src.portfolio.service import PortfolioService

__all__ = [
    "XrayRequest",
    "RebalanceNotesRequest",
    "ConstraintsRequest",
    "OptimizeRequest",
    "PortfolioSourceRequest",
    "PortfolioSettingsRequest",
    "register_portfolio_routes",
]

_REFRESH_LOCK = threading.Lock()
_REFRESH_OPERATION_LOCK = threading.Lock()
_RECONNECT_OPERATION_LOCK = threading.Lock()
_RECONNECT_STATE_LOCK = threading.Lock()
_RECONNECT_TIMEOUT_SECONDS = 330
_RECONNECT_STATE = {
    "running": False,
    "source_id": None,
    "status": "idle",
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_REFRESH_STATE = {
    "running": False,
    "current": None,
    "sources": {},
}


class PortfolioSourceRequest(BaseModel):
    connection_id: str
    label: str
    enabled: bool = True
    order: int = 0
    include_cash: bool = True


class PortfolioSettingsRequest(BaseModel):
    display_currency: str = Field(default="USD", pattern="^(USD|CNY)$")
    sources: list[PortfolioSourceRequest] = Field(default_factory=list, max_length=50)


def _set_refresh_progress(source_id: str, status: str, error: str | None) -> None:
    """Record one source's live refresh state for the polling endpoint.

    Args:
        source_id: The configured source being refreshed.
        status: ``pending``, ``refreshing``, ``ok`` or ``error``.
        error: Short failure text, or ``None``.
    """
    with _REFRESH_LOCK:
        _REFRESH_STATE["current"] = source_id if status == "refreshing" else None
        _REFRESH_STATE["sources"][source_id] = {"status": status, "error": error}

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

def _reconnect_snapshot() -> dict:
    with _RECONNECT_STATE_LOCK:
        return deepcopy(_RECONNECT_STATE)


def _set_reconnect_state(**updates) -> None:
    with _RECONNECT_STATE_LOCK:
        _RECONNECT_STATE.update(updates)


def _stop_reconnect_process(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run_reconnect(source_id: str) -> None:
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "src.portfolio.oauth_worker", source_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return_code = process.wait(timeout=_RECONNECT_TIMEOUT_SECONDS)
        if return_code == 0:
            _set_reconnect_state(status="authorized", error=None)
        else:
            _set_reconnect_state(
                status="error",
                error="Authorization did not complete. Close the old broker authorization tab and reconnect.",
            )
    except subprocess.TimeoutExpired:
        if process is not None:
            _stop_reconnect_process(process)
        _set_reconnect_state(
            status="timeout",
            error="Authorization timed out. The callback server was closed and reconnect is safe to retry.",
        )
    except Exception:
        if process is not None and process.poll() is None:
            _stop_reconnect_process(process)
        _set_reconnect_state(
            status="error", error="Unable to start the local authorization process."
        )
    finally:
        _set_reconnect_state(
            running=False,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        _RECONNECT_OPERATION_LOCK.release()


def register_portfolio_routes(app: FastAPI) -> None:
    """Register the authenticated, read-only portfolio endpoints.

    Args:
        app: The FastAPI application to mount the routes on.
    """

    def service(*, progress: bool = False) -> PortfolioService:
        return PortfolioService(
            progress_callback=_set_refresh_progress if progress else None
        )

    @app.get("/api/portfolio", dependencies=[Depends(require_auth)])
    def latest_portfolio():
        snapshot = service().latest()
        if snapshot is None:
            return {"status": "empty", "snapshot": None}
        return {"status": "ok", "snapshot": snapshot}

    @app.post("/api/portfolio/refresh", dependencies=[Depends(require_auth)])
    def refresh_portfolio():
        if not _REFRESH_OPERATION_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409, detail="portfolio refresh already running"
            )
        try:
            selected = [
                item for item in service().settings()["sources"] if item["enabled"]
            ]
            with _REFRESH_LOCK:
                _REFRESH_STATE.update(
                    running=True,
                    current=None,
                    sources={
                        item["connection_id"]: {"status": "pending", "error": None}
                        for item in selected
                    },
                )
            return {"status": "ok", "snapshot": service(progress=True).refresh()}
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            with _REFRESH_LOCK:
                _REFRESH_STATE["running"] = False
                _REFRESH_STATE["current"] = None
            _REFRESH_OPERATION_LOCK.release()

    @app.get("/api/portfolio/refresh-status", dependencies=[Depends(require_auth)])
    def portfolio_refresh_status():
        with _REFRESH_LOCK:
            refresh = deepcopy(_REFRESH_STATE)
            # ``brokers`` was the original dashboard field name. Keep it as a
            # compatibility alias so an already-open frontend can safely poll
            # a newer backend while its cached assets are being replaced.
            refresh["brokers"] = deepcopy(refresh["sources"])
            return {"status": "ok", "refresh": refresh}

    @app.post(
        "/api/portfolio/sources/{source_id}/reconnect",
        dependencies=[Depends(require_auth)],
        status_code=202,
    )
    def reconnect_portfolio_source(source_id: str):
        if not _RECONNECT_OPERATION_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409, detail="portfolio reconnect already running"
            )
        try:
            service().reconnect_target(source_id)
        except RuntimeError as exc:
            _RECONNECT_OPERATION_LOCK.release()
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            _RECONNECT_OPERATION_LOCK.release()
            raise HTTPException(
                status_code=503, detail="Unable to load the local OAuth configuration"
            ) from exc
        _set_reconnect_state(
            running=True,
            source_id=source_id,
            status="authorizing",
            error=None,
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
        )
        try:
            threading.Thread(
                target=_run_reconnect,
                args=(source_id,),
                daemon=True,
                name=f"portfolio-oauth-{source_id}",
            ).start()
        except Exception:
            _RECONNECT_OPERATION_LOCK.release()
            _set_reconnect_state(
                running=False,
                status="error",
                error="Unable to start the local authorization task.",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            raise HTTPException(
                status_code=503, detail="Unable to start the local authorization task"
            )
        return {"status": "started", "reconnect": _reconnect_snapshot()}

    @app.get("/api/portfolio/reconnect-status", dependencies=[Depends(require_auth)])
    def portfolio_reconnect_status():
        return {"status": "ok", "reconnect": _reconnect_snapshot()}

    @app.get("/api/portfolio/settings", dependencies=[Depends(require_auth)])
    def portfolio_settings():
        instance = service()
        return {
            "status": "ok",
            "settings": instance.settings(),
            "catalog": instance.sources(),
        }

    @app.put(
        "/api/portfolio/settings",
        dependencies=[Depends(require_settings_write_auth)],
    )
    def update_portfolio_settings(payload: PortfolioSettingsRequest):
        try:
            instance = service()
            settings = instance.save_settings(payload.model_dump())
            return {"status": "ok", "settings": settings, "catalog": instance.sources()}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/portfolio/history", dependencies=[Depends(require_auth)])
    def portfolio_history(limit: int = Query(180, ge=1, le=2000)):
        return {"status": "ok", "history": service().history(limit)}

    @app.get("/api/portfolio/analysis-context", dependencies=[Depends(require_auth)])
    def portfolio_analysis_context():
        context = service().analysis_context()
        if context is None:
            raise HTTPException(status_code=404, detail="no portfolio snapshot exists")
        return {"status": "ok", "context": context}

    @app.get("/api/portfolio/export.csv", dependencies=[Depends(require_auth)])
    def export_portfolio_csv():
        content = service().export_csv()
        if not content:
            raise HTTPException(status_code=404, detail="no portfolio snapshot exists")
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=portfolio.csv"},
        )

    """Mount the Portfolio Studio endpoints onto ``app``.

    Args:
        app: The FastAPI application to register routes on.
        require_auth: Auth dependency; when ``None`` it is resolved from the
            ``api_server`` module (mirroring ``register_options_lab_routes``).
    """

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
