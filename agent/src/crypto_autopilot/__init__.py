"""crypto_autopilot — 24/7 autonomous crypto trading loop.

Runs the full self-improving closed loop: collect → mine → evaluate →
backtest → paper-trade → promote → live-trade → feedback, with risk
kill switches, health monitoring, and LLM budget enforcement.

Public API:
    AutopilotConfig / load_autopilot_config  — configuration
    FactorLifecycle / PipelinePhase          — enums
    FactorCandidate / BacktestReport         — data models
    PaperPosition / PipelineState            — state containers
    can_transition / transition              — state machine
    advance_factor_lifecycle                 — factor lifecycle helper
"""

from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.crypto_autopilot.pipeline import (
    FACTOR_LIFECYCLE_ADVANCE,
    PIPELINE_TRANSITIONS,
    advance_factor_lifecycle,
    can_transition,
    transition,
)
from src.crypto_autopilot.types import (
    ALPHA_ID_PATTERN,
    BacktestReport,
    FactorCandidate,
    FactorLifecycle,
    PaperPosition,
    PipelinePhase,
    PipelineState,
    validate_alpha_id,
)

__all__ = [
    "ALPHA_ID_PATTERN",
    "AutopilotConfig",
    "BacktestReport",
    "FACTOR_LIFECYCLE_ADVANCE",
    "FactorCandidate",
    "FactorLifecycle",
    "PIPELINE_TRANSITIONS",
    "PaperPosition",
    "PipelinePhase",
    "PipelineState",
    "advance_factor_lifecycle",
    "can_transition",
    "load_autopilot_config",
    "transition",
    "validate_alpha_id",
]
# 后续阶段补充导出
