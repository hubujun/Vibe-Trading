"""BAB+high52w 双因子组合 routes — read-only dashboard data.

Mounted by ``agent/api_server.py`` via ``register_combo_routes(app)``.

- ``GET /api/combo/summary`` — aggregated view of the two-factor combo:
  latest daily signal, paper track record (nav + trades), backtest metrics
  (annual/sharpe/max-dd per variant), factor IC stats, and the linked
  hypothesis registry entries. Pure read of persisted artifacts under
  ``<agent>/runs/paper_combo`` and ``~/.vibe-trading/hypotheses.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = ["ComboSummary", "register_combo_routes"]

#: Combo runtime root — where daily_signal.py / combo_backtest.py persist.
#: NB: Vibe-Trading migrates agent/runs/* to ~/.vibe-trading/runs/ on boot,
#: so keep the canonical path under the home dir to avoid split-brain.
_RUNTIME_ROOT = Path.home() / ".vibe-trading" / "runs" / "paper_combo"


# ============================================================================
# Pydantic Models
# ============================================================================


class ComboSignalItem(BaseModel):
    symbol: str
    score: float


class ComboSignal(BaseModel):
    date: Optional[str] = None
    longs: list[ComboSignalItem] = Field(default_factory=list)
    shorts: list[ComboSignalItem] = Field(default_factory=list)


class ComboPaper(BaseModel):
    nav: Optional[float] = None
    started_at: Optional[str] = None
    last_signal_date: Optional[str] = None
    trades: list[dict[str, Any]] = Field(default_factory=list)


class ComboMetrics(BaseModel):
    updated_at: Optional[str] = None
    period: Optional[str] = None
    symbols: Optional[int] = None
    days: Optional[int] = None
    cost_per_side: Optional[float] = None
    ic: dict[str, dict[str, float]] = Field(default_factory=dict)
    backtest: dict[str, dict[str, float]] = Field(default_factory=dict)


class ComboHypothesis(BaseModel):
    hypothesis_id: str
    title: str
    status: str
    thesis: str = ""


class ComboSummary(BaseModel):
    signal: ComboSignal = Field(default_factory=ComboSignal)
    paper: ComboPaper = Field(default_factory=ComboPaper)
    metrics: ComboMetrics = Field(default_factory=ComboMetrics)
    hypotheses: list[ComboHypothesis] = Field(default_factory=list)
    updated_at: Optional[str] = None


# ============================================================================
# Loaders (fail-open)
# ============================================================================


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _load_signal() -> ComboSignal:
    raw = _read_json(_RUNTIME_ROOT / "state.json")
    scores = raw.get("scores", {}) or {}
    longs = [
        ComboSignalItem(symbol=str(s), score=float(scores.get(s, 0)))
        for s in raw.get("last_longs", [])
    ]
    shorts = [
        ComboSignalItem(symbol=str(s), score=float(scores.get(s, 0)))
        for s in raw.get("last_shorts", [])
    ]
    return ComboSignal(
        date=str(raw.get("last_signal_date", "")),
        longs=longs,
        shorts=shorts,
    )


def _load_paper() -> ComboPaper:
    raw = _read_json(_RUNTIME_ROOT / "state.json")
    return ComboPaper(
        nav=raw.get("nav"),
        started_at=raw.get("started_at"),
        last_signal_date=raw.get("last_signal_date"),
        trades=raw.get("trades", [])[-20:],
    )


def _load_metrics() -> ComboMetrics:
    raw = _read_json(_RUNTIME_ROOT / "backtest_metrics.json")
    return ComboMetrics(**{k: v for k, v in raw.items() if k in ComboMetrics.model_fields})


def _load_hypotheses() -> list[ComboHypothesis]:
    path = Path.home() / ".vibe-trading" / "hypotheses.json"
    raw = _read_json(path)
    records = raw if isinstance(raw, list) else raw.get("hypotheses", [])
    out = []
    for h in records:
        try:
            out.append(
                ComboHypothesis(
                    hypothesis_id=str(h.get("hypothesis_id", "")),
                    title=str(h.get("title", "")),
                    status=str(h.get("status", "")),
                    thesis=str(h.get("thesis", "")),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


# ============================================================================
# Registration
# ============================================================================


def register_combo_routes(
    app: FastAPI,
    require_auth: Any | None = None,
) -> None:
    """Mount the combo dashboard routes onto ``app``."""

    @app.get("/api/combo/summary", response_model=ComboSummary)
    async def combo_summary() -> ComboSummary:
        return ComboSummary(
            signal=_load_signal(),
            paper=_load_paper(),
            metrics=_load_metrics(),
            hypotheses=_load_hypotheses(),
            updated_at=str(_RUNTIME_ROOT.joinpath("state.json").stat().st_mtime)
            if _RUNTIME_ROOT.joinpath("state.json").exists()
            else None,
        )
