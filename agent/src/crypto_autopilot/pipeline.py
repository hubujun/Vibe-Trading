"""State-machine definitions and pure transition functions for crypto_autopilot.

Two orthogonal state machines live here:

1. :class:`~src.crypto_autopilot.types.PipelinePhase` — the 24/7 loop phase.
2. :class:`~src.crypto_autopilot.types.FactorLifecycle` — a single factor's stage.

Both are pure: :func:`can_transition` queries legality and :func:`transition`
returns a *new* :class:`~src.crypto_autopilot.types.PipelineState` (the input
is never mutated). This mirrors the functional style of
:mod:`src.scheduled_research.executor` where schedule math is pure and
clock-injected.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from src.crypto_autopilot.types import (
    FactorLifecycle,
    PipelinePhase,
    PipelineState,
)

__all__ = [
    "PIPELINE_TRANSITIONS",
    "FACTOR_LIFECYCLE_ADVANCE",
    "can_transition",
    "transition",
    "advance_factor_lifecycle",
]

# ---------------------------------------------------------------------------
# Pipeline phase transitions
# ---------------------------------------------------------------------------

#: Legal forward transitions for the 24/7 autopilot loop.
#:
#: - IDLE → COLLECTING (boot / new cycle)
#: - COLLECTING → DISCOVERING (data gathered, start mining)
#: - DISCOVERING → BACKTESTING (factor candidate produced)
#: - BACKTESTING → PAPER_TRADING (passed admission gate)
#: - BACKTESTING → FEEDBACK (failed gate, recycle)
#: - PAPER_TRADING → LIVE (paper targets met)
#: - PAPER_TRADING → FEEDBACK (paper targets missed, recycle)
#: - LIVE → FEEDBACK (live cycle complete or interrupted, recycle)
#: - FEEDBACK → COLLECTING (insights folded back, start next cycle)
PIPELINE_TRANSITIONS: dict[PipelinePhase, frozenset[PipelinePhase]] = {
    PipelinePhase.IDLE: frozenset({PipelinePhase.COLLECTING}),
    PipelinePhase.COLLECTING: frozenset({PipelinePhase.DISCOVERING}),
    PipelinePhase.DISCOVERING: frozenset({PipelinePhase.BACKTESTING}),
    PipelinePhase.BACKTESTING: frozenset({
        PipelinePhase.PAPER_TRADING,
        PipelinePhase.FEEDBACK,
    }),
    PipelinePhase.PAPER_TRADING: frozenset({
        PipelinePhase.LIVE,
        PipelinePhase.FEEDBACK,
    }),
    PipelinePhase.LIVE: frozenset({PipelinePhase.FEEDBACK}),
    PipelinePhase.FEEDBACK: frozenset({PipelinePhase.COLLECTING}),
}

# ---------------------------------------------------------------------------
# Factor lifecycle transitions
# ---------------------------------------------------------------------------

#: Linear happy-path advancement map for a factor's lifecycle.
#:
#: DISCOVERED → BACKTESTED → PAPER_VALIDATED → LIVE_DEPLOYED → RETIRED
#:
#: Abnormal retirement (any → RETIRED) is handled by the caller, not here —
#: this map only describes the *normal* next stage.
FACTOR_LIFECYCLE_ADVANCE: dict[FactorLifecycle, FactorLifecycle] = {
    FactorLifecycle.DISCOVERED: FactorLifecycle.BACKTESTED,
    FactorLifecycle.BACKTESTED: FactorLifecycle.PAPER_VALIDATED,
    FactorLifecycle.PAPER_VALIDATED: FactorLifecycle.LIVE_DEPLOYED,
    FactorLifecycle.LIVE_DEPLOYED: FactorLifecycle.RETIRED,
}


def can_transition(from_phase: PipelinePhase, to_phase: PipelinePhase) -> bool:
    """Return whether *to_phase* is a legal transition from *from_phase*.

    Args:
        from_phase: The current pipeline phase.
        to_phase: The candidate target phase.

    Returns:
        ``True`` if the transition is permitted by ``PIPELINE_TRANSITIONS``.
    """
    return to_phase in PIPELINE_TRANSITIONS.get(from_phase, frozenset())


def transition(state: PipelineState, to_phase: PipelinePhase) -> PipelineState:
    """Return a *new* :class:`PipelineState` advanced to *to_phase*.

    Pure: the input *state* is never mutated. Raises ``ValueError`` when the
    transition is illegal (call :func:`can_transition` first to check).

    Args:
        state: The current pipeline state snapshot.
        to_phase: The target phase.

    Returns:
        A new :class:`PipelineState` with ``phase`` set to *to_phase* and
        ``updated_at`` refreshed to now (UTC).

    Raises:
        ValueError: If the transition is not in ``PIPELINE_TRANSITIONS``.
    """
    if not can_transition(state.phase, to_phase):
        raise ValueError(
            f"illegal pipeline transition: {state.phase.value} → {to_phase.value}"
        )
    now = datetime.now(timezone.utc)
    return replace(state, phase=to_phase, updated_at=now)


def advance_factor_lifecycle(current: FactorLifecycle) -> FactorLifecycle | None:
    """Return the next lifecycle stage after *current*, or ``None`` if terminal.

    The normal advancement path is::

        DISCOVERED → BACKTESTED → PAPER_VALIDATED → LIVE_DEPLOYED → RETIRED

    :attr:`~src.crypto_autopilot.types.FactorLifecycle.RETIRED` is terminal
    and returns ``None``. Abnormal retirement (any → RETIRED) is handled by
    the caller, not here — this function only describes the *happy-path*
    next stage.

    Args:
        current: The current factor lifecycle stage.

    Returns:
        The next stage, or ``None`` if *current* is terminal (``RETIRED``).
    """
    return FACTOR_LIFECYCLE_ADVANCE.get(current)
