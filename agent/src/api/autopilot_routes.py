"""Crypto autopilot HTTP routes — read-only observability panel.

Mounted by ``agent/api_server.py`` via ``register_autopilot_routes(app)``.

- ``GET /api/autopilot/status`` — aggregated pipeline state + liveness +
  halt sentinel + daily order counter + config summary, so a Web UI (or any
  external watchdog) can render the 24/7 loop's health without touching the
  filesystem directly.

Everything here is a pure read of the autopilot's persisted artifacts
(``HealthMonitor`` state/heartbeat, the ``HALT`` sentinel, and the
``DailyOrderCounter`` payload). No write path is exposed — start/stop stays
in the CLI (``vibe-trading autopilot``) and the kill switch stays on the
surface layer (``POST /live/halt``).
"""

from __future__ import annotations

import logging
import sys as _sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = [
    "AutopilotStatusResponse",
    "AutopilotPipelineState",
    "AutopilotHealthState",
    "AutopilotHaltState",
    "AutopilotDailyCounter",
    "AutopilotConfigSummary",
    "register_autopilot_routes",
]

#: Broker key used by the autopilot's kill switch (matches cli_entry).
_BROKER_KEY = "okx"

#: Autopilot runtime root — the same directory family HealthMonitor and the
#: daily counter persist under (``<agent>/runs/autopilot``).
_RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "runs" / "autopilot"


# ============================================================================
# Pydantic Models
# ============================================================================


class AutopilotPipelineState(BaseModel):
    """Snapshot of the pipeline phase and tick counters."""

    phase: str = Field(..., description="Current PipelinePhase value")
    active_factor_id: Optional[str] = Field(None, description="Factor currently in the pipeline")
    tick_count: int = Field(0, description="Total ticks processed since boot")
    last_tick_at: Optional[str] = Field(None, description="UTC ISO timestamp of the last tick")
    updated_at: Optional[str] = Field(None, description="UTC ISO timestamp of the last mutation")
    regime: Optional[Dict[str, Any]] = Field(None, description="Latest market-regime classification (Phase 3)")


class AutopilotHealthState(BaseModel):
    """Liveness signals derived from the heartbeat + state files."""

    alive: bool = Field(..., description="Fresh heartbeat AND a state file exist")
    stale: bool = Field(..., description="Heartbeat older than the staleness window")
    heartbeat_ms: Optional[int] = Field(None, description="Last heartbeat epoch-ms, if any")


class AutopilotHaltState(BaseModel):
    """Kill-switch sentinel status for the autopilot broker."""

    halted: bool = Field(..., description="Whether the broker is halted")
    reason: Optional[str] = Field(None, description="Trip reason recorded in the sentinel")
    tripped_by: Optional[str] = Field(None, description="Source that tripped the sentinel")
    tripped_at: Optional[str] = Field(None, description="UTC ISO timestamp of the trip")


class AutopilotDailyCounter(BaseModel):
    """Persisted per-UTC-day order count."""

    date: str = Field(..., description="UTC date (YYYY-MM-DD) the count belongs to")
    count: int = Field(0, description="Orders placed on that date")


class AutopilotConfigSummary(BaseModel):
    """Operator-relevant tuning knobs (not the full config surface)."""

    enabled: bool
    pairs: list[str]
    max_order_notional_usd: float
    max_total_exposure_usd: float
    max_trades_per_day: int
    mine_interval_hours: int
    evaluate_interval_hours: int
    trade_interval_minutes: int
    feedback_interval_hours: int


class AutopilotDataHealthSymbol(BaseModel):
    """Per-symbol freshness inside the data-health summary."""

    latest_ts: Optional[str] = None
    lag_hours: Optional[float] = None


class AutopilotDataHealth(BaseModel):
    """Market-data freshness snapshot for the dashboard."""

    updated_at: Optional[str] = None
    stale_symbols: list[str] = []
    symbols: dict[str, AutopilotDataHealthSymbol] = {}


class AutopilotStatusResponse(BaseModel):
    """Aggregated status payload for the autopilot dashboard."""

    pipeline: AutopilotPipelineState
    health: AutopilotHealthState
    halt: AutopilotHaltState
    counter: AutopilotDailyCounter
    config: AutopilotConfigSummary
    data_health: AutopilotDataHealth = Field(default_factory=AutopilotDataHealth)


class AutopilotTradeRecord(BaseModel):
    """One fill from the unified paper/live trade ledger."""

    ts: str = Field(..., description="UTC ISO-8601 fill timestamp")
    engine: str = Field(..., description="'paper' or 'live'")
    symbol: str = Field(..., description="Instrument id, e.g. BTC-USDT")
    side: str = Field(..., description="buy or sell")
    quantity: Optional[float] = Field(None, description="Filled base quantity")
    price: Optional[float] = Field(None, description="Fill price")
    notional: float = Field(..., description="Quote-currency amount (USD)")
    realized_pnl: Optional[float] = Field(None, description="Realized P&L on close")
    alpha_id: Optional[str] = Field(None, description="Triggering factor id")


class AutopilotFactorInfo(BaseModel):
    """One factor's lifecycle snapshot for the dashboard."""

    alpha_id: str
    lifecycle: str = ""
    screen_ic_mean: Optional[float] = None
    ic_mean: Optional[float] = None
    ir: Optional[float] = None
    alpha_t_full: Optional[float] = None
    alpha_t_train: Optional[float] = None
    alpha_t_test: Optional[float] = None
    category: Optional[str] = None


class AutopilotRetiredFactor(BaseModel):
    """One retired factor's audit entry (why + when)."""

    alpha_id: str
    retired_at: Optional[str] = None
    reason: Optional[str] = None


class AutopilotZooFactor(BaseModel):
    """One mined factor in the zoo inventory with its metadata.

    ``meta`` mirrors the factor's ``__alpha_meta__`` (nickname, theme,
    formula_latex, universe, frequency, decay_horizon, min_warmup_bars,
    notes). It is ``None`` when the metadata could not be parsed.
    """

    alpha_id: str
    meta: Optional[dict[str, Any]] = None
    meta_ok: bool = False


class AutopilotFactorListResponse(BaseModel):
    """Active/pending/retired factors plus the full zoo inventory."""

    active: list[AutopilotFactorInfo] = []
    pending: list[str] = []
    retired: list[AutopilotRetiredFactor] = []
    zoo: list[AutopilotZooFactor] = []
    updated_at: Optional[str] = None


class AutopilotDailyPnl(BaseModel):
    """One day of realized paper P&L."""

    date: str
    pnl_usd: float


class AutopilotPosition(BaseModel):
    """One open paper-trading position with mark-to-market P&L."""

    symbol: str
    side: str
    quantity: float
    entry_price: float
    entry_time: Optional[str] = None
    unrealized_pnl: float = 0.0


class AutopilotPositionsResponse(BaseModel):
    """Current open paper positions."""

    positions: list[AutopilotPosition] = []
    count: int = 0


class AutopilotPerformanceResponse(BaseModel):
    """Paper-account performance metrics derived from the ledger."""

    total_trades: int = 0
    open_positions: int = 0
    open_exposure_usd: float = 0.0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    realized_pnl_usd: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    daily_pnl: list[AutopilotDailyPnl] = []
    benchmark_symbol: Optional[str] = None
    benchmark_return_pct: Optional[float] = None
    avg_slippage_bps: Optional[float] = None


class AutopilotTradesResponse(BaseModel):
    """Newest-first trade ledger slice for the dashboard."""

    trades: list[AutopilotTradeRecord]
    count: int = Field(..., description="Number of records returned")


class AutopilotGapEngineStats(BaseModel):
    """One engine's fill aggregation inside the gap report."""

    count: int = 0
    avg_price: Optional[float] = None
    total_fee: float = 0.0


class AutopilotGapEntry(BaseModel):
    """Paper-vs-live comparison for one symbol or factor."""

    paper: AutopilotGapEngineStats = Field(default_factory=AutopilotGapEngineStats)
    live: AutopilotGapEngineStats = Field(default_factory=AutopilotGapEngineStats)
    price_gap_bps: Optional[float] = None


class AutopilotSlippageSummary(BaseModel):
    """Slippage summary inside the gap report."""

    records: int = 0
    avg_bps: Optional[float] = None
    max_bps: Optional[float] = None


class AutopilotGapResponse(BaseModel):
    """Paper-vs-live gap report over the trailing window."""

    generated_at: str = Field(..., description="UTC ISO report timestamp")
    window_days: int = 7
    by_symbol: dict[str, AutopilotGapEntry] = {}
    by_factor: dict[str, AutopilotGapEntry] = {}
    slippage: AutopilotSlippageSummary = Field(default_factory=AutopilotSlippageSummary)
    live_scale: float = 5.0
    live_scale_state: dict[str, Any] = {}


# ============================================================================
# Route registration
# ============================================================================

AuthDep = Callable[..., Awaitable[Any] | Any]


def _load_pipeline_state() -> AutopilotPipelineState:
    """Read the persisted pipeline snapshot, fail-closed to idle defaults."""
    from src.crypto_autopilot.health import HealthMonitor

    state = HealthMonitor(_RUNTIME_ROOT).load_pipeline_state()
    if state is None:
        return AutopilotPipelineState(phase="idle")
    return AutopilotPipelineState(
        phase=state.phase.value,
        active_factor_id=state.active_factor_id,
        tick_count=state.tick_count,
        last_tick_at=state.last_tick_at.isoformat() if state.last_tick_at else None,
        updated_at=state.updated_at.isoformat() if state.updated_at else None,
        regime=state.regime,
    )


def _load_health() -> AutopilotHealthState:
    """Read liveness signals, treating unreadable artifacts as not-alive."""
    from src.crypto_autopilot.health import HealthMonitor

    health = HealthMonitor(_RUNTIME_ROOT)
    return AutopilotHealthState(
        alive=health.is_alive(),
        stale=health.is_stale(),
        heartbeat_ms=health._read_heartbeat_ts(),
    )


def _load_halt() -> AutopilotHaltState:
    """Read the kill-switch sentinel for the autopilot broker."""
    from src.live.halt import halt_flag_set, read_halt

    halted = halt_flag_set(_BROKER_KEY)
    meta = read_halt(_BROKER_KEY) or {}
    return AutopilotHaltState(
        halted=halted,
        reason=meta.get("reason"),
        tripped_by=meta.get("by"),
        tripped_at=meta.get("tripped_at"),
    )


def _load_counter() -> AutopilotDailyCounter:
    """Read the persisted daily order count (fail-open to zero)."""
    import json

    from src.crypto_autopilot.daily_counter import COUNTER_FILENAME, DailyOrderCounter

    counter = DailyOrderCounter(_RUNTIME_ROOT)
    date = "unknown"
    try:
        raw = json.loads(
            (_RUNTIME_ROOT / COUNTER_FILENAME).read_text(encoding="utf-8")
        )
        date = str(raw.get("date", "unknown"))
    except (OSError, ValueError, TypeError):
        pass
    return AutopilotDailyCounter(date=date, count=counter.count_today())


def _load_data_health() -> AutopilotDataHealth:
    """Read the persisted market-data freshness snapshot (fail-open)."""
    import json

    try:
        raw = json.loads(
            (_RUNTIME_ROOT / "data_health.json").read_text(encoding="utf-8")
        )
        return AutopilotDataHealth(
            updated_at=raw.get("updated_at"),
            stale_symbols=[str(s) for s in raw.get("stale_symbols", [])],
            symbols={
                str(k): AutopilotDataHealthSymbol(**v)
                for k, v in raw.get("symbols", {}).items()
            },
        )
    except (OSError, ValueError, TypeError):
        return AutopilotDataHealth()


def register_autopilot_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount the autopilot status routes onto ``app``.

    Args:
        app: The FastAPI application to register routes on.
        require_auth: Auth dependency; when ``None`` it is resolved from the
            ``api_server`` module (mirroring ``register_live_routes``).
    """
    h = _sys.modules.get("api_server")
    if h is None:
        raise RuntimeError(
            "register_autopilot_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )
    if require_auth is None:
        require_auth = h.require_auth

    @app.get(
        "/api/autopilot/status",
        response_model=AutopilotStatusResponse,
        dependencies=[Depends(require_auth)],
    )
    async def autopilot_status_endpoint() -> AutopilotStatusResponse:
        """Return aggregated autopilot status for the dashboard."""
        from src.crypto_autopilot.config import load_autopilot_config

        config = load_autopilot_config()
        config_summary = AutopilotConfigSummary(
            enabled=config.enabled,
            pairs=list(config.pairs),
            max_order_notional_usd=config.max_order_notional_usd,
            max_total_exposure_usd=config.max_total_exposure_usd,
            max_trades_per_day=config.max_trades_per_day,
            mine_interval_hours=config.mine_interval_hours,
            evaluate_interval_hours=config.evaluate_interval_hours,
            trade_interval_minutes=config.trade_interval_minutes,
            feedback_interval_hours=config.feedback_interval_hours,
        )
        return AutopilotStatusResponse(
            pipeline=_load_pipeline_state(),
            health=_load_health(),
            halt=_load_halt(),
            counter=_load_counter(),
            config=config_summary,
            data_health=_load_data_health(),
        )

    @app.get(
        "/api/autopilot/trades",
        response_model=AutopilotTradesResponse,
        dependencies=[Depends(require_auth)],
    )
    async def autopilot_trades_endpoint(
        engine: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> AutopilotTradesResponse:
        """Return the unified paper/live trade ledger, newest first."""
        from src.crypto_autopilot.trade_ledger import read_trade_records

        records = read_trade_records(
            _RUNTIME_ROOT, limit=limit, engine=engine, symbol=symbol,
        )
        trades = [AutopilotTradeRecord(**record) for record in records]
        return AutopilotTradesResponse(trades=trades, count=len(trades))

    @app.get(
        "/api/autopilot/factors",
        response_model=AutopilotFactorListResponse,
        dependencies=[Depends(require_auth)],
    )
    async def autopilot_factors_endpoint() -> AutopilotFactorListResponse:
        """Return active/pending factors plus the mined zoo inventory."""
        import json

        from src.crypto_autopilot.factor_store import FactorStore

        payload: dict = {}
        try:
            payload = json.loads(
                (_RUNTIME_ROOT / "factors.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            pass
        zoo = FactorStore().list_factors_with_meta()
        return AutopilotFactorListResponse(
            active=[
                AutopilotFactorInfo(**f) for f in payload.get("active", [])
            ],
            pending=[str(x) for x in payload.get("pending", [])],
            retired=[
                AutopilotRetiredFactor(**f) for f in payload.get("retired", [])
            ],
            zoo=[AutopilotZooFactor(**entry) for entry in zoo],
            updated_at=payload.get("updated_at"),
        )

    @app.get(
        "/api/autopilot/positions",
        response_model=AutopilotPositionsResponse,
        dependencies=[Depends(require_auth)],
    )
    async def autopilot_positions_endpoint() -> AutopilotPositionsResponse:
        """Return current open paper positions with unrealized P&L."""
        from src.crypto_autopilot.config import load_autopilot_config
        from src.crypto_autopilot.paper_engine import PaperEngine

        engine = PaperEngine(
            config=load_autopilot_config(), runtime_root=_RUNTIME_ROOT,
        )
        try:
            raw = engine.get_positions()
        except Exception:  # noqa: BLE001 — pricing is best-effort
            raw = []
        positions = [
            AutopilotPosition(
                symbol=p.symbol,
                side=p.side,
                quantity=round(float(p.quantity), 8),
                entry_price=round(float(p.entry_price), 4),
                entry_time=(
                    p.entry_time.isoformat()
                    if hasattr(p.entry_time, "isoformat")
                    else str(p.entry_time)
                ),
                unrealized_pnl=round(float(p.unrealized_pnl), 4),
            )
            for p in raw
        ]
        return AutopilotPositionsResponse(positions=positions, count=len(positions))

    @app.get(
        "/api/autopilot/performance",
        response_model=AutopilotPerformanceResponse,
        dependencies=[Depends(require_auth)],
    )
    async def autopilot_performance_endpoint() -> AutopilotPerformanceResponse:
        """Paper-account metrics derived from the persisted trade ledger."""
        import numpy as np

        from src.crypto_autopilot.config import load_autopilot_config
        from src.crypto_autopilot.paper_engine import PaperEngine
        from src.crypto_autopilot.trade_ledger import (
            read_slippage_records,
            read_trade_records,
        )

        records = read_trade_records(_RUNTIME_ROOT, limit=10_000)

        # Average signal-vs-fill spread in bps across all slippage
        # measurements (best-effort; ``None`` when nothing measured yet).
        slip = read_slippage_records(_RUNTIME_ROOT)
        avg_slippage_bps = (
            round(sum(float(r.get("bps", 0.0)) for r in slip) / len(slip), 2)
            if slip else None
        )
        wins = losses = 0
        realized = 0.0
        daily: dict[str, float] = {}
        for rec in records:
            pnl = rec.get("realized_pnl")
            if pnl is None:
                continue
            realized += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            day = str(rec.get("ts", ""))[:10]
            if day:
                daily[day] = daily.get(day, 0.0) + pnl
        closed = wins + losses

        engine = PaperEngine(
            config=load_autopilot_config(), runtime_root=_RUNTIME_ROOT,
        )
        try:
            exposure = engine.open_exposure_usd()
        except Exception:  # noqa: BLE001 — pricing is best-effort
            exposure = 0.0

        days = [
            AutopilotDailyPnl(date=d, pnl_usd=round(v, 2))
            for d, v in sorted(daily.items())
        ]
        sharpe = 0.0
        max_dd = 0.0
        if len(days) >= 2:
            values = np.array([d.pnl_usd for d in days], dtype=np.float64)
            std = float(np.std(values, ddof=1))
            if std > 0:
                sharpe = float(np.mean(values)) / std * np.sqrt(365)
            cum = np.cumsum(values)
            running_max = np.maximum.accumulate(cum)
            peak = float(np.max(running_max))
            if peak > 0:
                max_dd = float(np.max(running_max - cum)) / peak

        config = load_autopilot_config()
        benchmark_symbol = config.benchmark_symbol
        benchmark_return_pct = None
        if days:
            start_date = days[0].date
            end_date = days[-1].date
            try:
                bars = engine.feed.fetch_bars(
                    benchmark_symbol, period="1d", limit=400,
                )
                if bars is not None and not bars.empty:
                    closes = bars["close"] if "close" in bars else None
                    if closes is not None and len(closes) >= 2:
                        start_price = None
                        end_price = float(closes.iloc[-1])
                        for idx in range(len(closes)):
                            if str(closes.index[idx])[:10] >= start_date:
                                start_price = float(closes.iloc[idx])
                                break
                        if start_price is None:
                            start_price = float(closes.iloc[0])
                        if start_price > 0:
                            benchmark_return_pct = round(
                                (end_price / start_price - 1.0) * 100.0, 2,
                            )
            except Exception:  # noqa: BLE001 — benchmark is best-effort
                benchmark_return_pct = None

        return AutopilotPerformanceResponse(
            total_trades=len(records),
            open_positions=sum(1 for f in engine._positions.values() if f),
            open_exposure_usd=round(exposure, 2),
            wins=wins,
            losses=losses,
            win_rate=round(wins / closed, 4) if closed else 0.0,
            realized_pnl_usd=round(realized, 2),
            sharpe=round(sharpe, 4),
            max_drawdown=round(max_dd, 4),
            daily_pnl=days,
            benchmark_symbol=benchmark_symbol if benchmark_return_pct is not None else None,
            benchmark_return_pct=benchmark_return_pct,
            avg_slippage_bps=avg_slippage_bps,
        )

    @app.get(
        "/api/autopilot/gap",
        response_model=AutopilotGapResponse,
        dependencies=[Depends(require_auth)],
    )
    async def autopilot_gap_endpoint(
        days: int = 7,
    ) -> AutopilotGapResponse:
        """Paper-vs-live execution gap report (shadow phase)."""
        from src.crypto_autopilot.config import load_autopilot_config
        from src.crypto_autopilot.gap_report import build_gap_report
        from src.crypto_autopilot.live_scale import load_live_scale

        report = build_gap_report(_RUNTIME_ROOT, days=days)
        config = load_autopilot_config()
        scale_state = load_live_scale(
            _RUNTIME_ROOT, initial=config.live_order_scale,
        )
        return AutopilotGapResponse(
            generated_at=report["generated_at"],
            window_days=report["window_days"],
            by_symbol={
                k: AutopilotGapEntry(**v) for k, v in report["by_symbol"].items()
            },
            by_factor={
                k: AutopilotGapEntry(**v) for k, v in report["by_factor"].items()
            },
            slippage=AutopilotSlippageSummary(**report["slippage"]),
            live_scale=float(scale_state["scale"]),
            live_scale_state=scale_state,
        )
