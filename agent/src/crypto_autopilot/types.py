"""Core data types for the crypto_autopilot autonomous trading pipeline.

Frozen dataclasses (no Pydantic): the pipeline state and factor candidates
flow through pure functions, so plain frozen dataclasses give the strongest
immutability guarantee with zero validation surface — matching the style of
:mod:`src.live.mandate.model` and :mod:`src.shadow_account.models`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

#: Regex that every mined alpha_id must satisfy. Mirrors the registry rule
#: ``^[a-z][a-z0-9]+_[a-z0-9_]+$`` from
#: :class:`src.factors.registry.AlphaMeta` — a mined factor must be a valid
#: zoo citizen before it can be backtested or paper-traded.
ALPHA_ID_PATTERN: str = r"^[a-z][a-z0-9]+_[a-z0-9_]+$"
_ALPHA_ID_RE = re.compile(ALPHA_ID_PATTERN)


def validate_alpha_id(alpha_id: str) -> None:
    """Raise ``ValueError`` when *alpha_id* does not match the zoo id rule.

    A mined factor id must be a valid alpha id (``^[a-z][a-z0-9]+_[a-z0-9_]+$``)
    so it can be registered as a zoo citizen in
    :mod:`src.factors.registry` without renaming.

    Args:
        alpha_id: The candidate factor identifier.

    Raises:
        ValueError: If *alpha_id* is empty or does not match the pattern.
    """
    if not alpha_id or not _ALPHA_ID_RE.fullmatch(alpha_id):
        raise ValueError(
            f"invalid alpha_id {alpha_id!r}: must match {ALPHA_ID_PATTERN}"
        )


class FactorLifecycle(str, Enum):
    """Lifecycle stages of a mined factor candidate.

    A factor advances linearly through these stages; any stage may
    short-circuit to :attr:`RETIRED` on failure or obsolescence.
    """

    DISCOVERED = "discovered"
    BACKTESTED = "backtested"
    PAPER_VALIDATED = "paper_validated"
    LIVE_DEPLOYED = "live_deployed"
    RETIRED = "retired"


class PipelinePhase(str, Enum):
    """Phases of the 24/7 autopilot loop.

    The pipeline cycles through these phases continuously — see
    :mod:`crypto_autopilot.pipeline` for the legal transition map.
    """

    IDLE = "idle"
    COLLECTING = "collecting"
    DISCOVERING = "discovering"
    BACKTESTING = "backtesting"
    PAPER_TRADING = "paper_trading"
    LIVE = "live"
    FEEDBACK = "feedback"


@dataclass(frozen=True)
class FactorCandidate:
    """A factor discovered by the mining phase, awaiting backtest validation.

    Attributes:
        alpha_id: Zoo-compatible factor id (matches
            ``^[a-z][a-z0-9]+_[a-z0-9_]+$``). Validated on construction.
        source_code: Raw Python source of the ``compute()`` function.
        created_at: UTC timestamp the candidate was first registered.
        zoo: Zoo bucket the factor belongs to (default ``"crypto_mined"``).
        lifecycle: Current :class:`FactorLifecycle` stage.
        meta: Free-form metadata dict (prompt, metrics, provenance, etc.).
    """

    alpha_id: str
    source_code: str
    created_at: datetime
    zoo: str = "crypto_mined"
    lifecycle: FactorLifecycle = FactorLifecycle.DISCOVERED
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the alpha_id on construction."""
        validate_alpha_id(self.alpha_id)


@dataclass(frozen=True)
class BacktestReport:
    """Result of a backtest run for one factor candidate.

    Attributes:
        alpha_id: Factor identifier that was backtested.
        run_dir: Filesystem path to the backtest run artifacts.
        status: Run status string (e.g. ``"ok"``, ``"error"``).
        metrics: Performance metrics dict (Sharpe, max drawdown, etc.).
        validation: Gate-check results dict.
        equity_curve: List of (timestamp, equity) tuples.
        trades: List of trade record dicts.
        passed_gate: Whether the backtest passed the admission gate.
        created_at: UTC timestamp the report was generated.
    """

    alpha_id: str
    run_dir: str
    status: str
    metrics: dict[str, Any]
    validation: dict[str, Any]
    equity_curve: list[Any]
    trades: list[Any]
    passed_gate: bool
    created_at: datetime


@dataclass(frozen=True)
class PaperPosition:
    """An open position in the paper-trading shadow account.

    Attributes:
        symbol: Trading pair, e.g. ``"BTC-USDT"``.
        side: ``"long"`` or ``"short"``.
        quantity: Position size in base units.
        entry_price: Average entry price, USD.
        entry_time: UTC timestamp the position was opened.
        unrealized_pnl: Current unrealized P&L, USD.
    """

    symbol: str
    side: str
    quantity: float
    entry_price: float
    entry_time: datetime
    unrealized_pnl: float = 0.0


@dataclass(frozen=True)
class PipelineState:
    """Snapshot of the autopilot loop's current phase and counters.

    Attributes:
        phase: Current :class:`PipelinePhase`.
        active_factor_id: Alpha id of the factor currently in the pipeline,
            or ``None`` when the pipeline is idle.
        last_tick_at: UTC timestamp of the most recent tick, or ``None``.
        tick_count: Total number of ticks processed since boot.
        updated_at: UTC timestamp this state was last mutated.
    """

    phase: PipelinePhase = PipelinePhase.IDLE
    active_factor_id: str | None = None
    last_tick_at: datetime | None = None
    tick_count: int = 0
    updated_at: datetime | None = None
