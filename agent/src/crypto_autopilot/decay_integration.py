"""SDM decay integration — auto-retire decaying autopilot factors.

The autopilot registers its mined factors with the Strategy Development
Manager's strategy store (see :class:`FactorStore`), which feeds the
existing IC/Sharpe decay monitor (:mod:`src.strategy_store.decay`).  This
module closes the loop: it runs the same decay scan the ``sdm_decay_scan``
tool performs — but scoped to the ``crypto`` universe — and when an
artifact reaches the terminal ``DISABLED`` state it **retires the factor**
in the autopilot's own lifecycle (:class:`FactorLifecycle.RETIRED` via
:class:`FactorStore`) and notifies operators through the IM outbox
(:mod:`src.crypto_autopilot.notifier`, ``factor_retired`` kind).

The manager is best-effort throughout: a strategy store that is
unavailable, a broken bench history, or a notification failure never
raises into the autopilot tick loop.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.crypto_autopilot.notifier import AutopilotNotifier
from src.crypto_autopilot.types import FactorLifecycle
from src.strategy_store.decay import DecayEvaluator
from src.strategy_store.metrics import compute_decay_metrics, has_decay_inputs
from src.strategy_store.models import ArtifactStatus, DecaySnapshot

logger = logging.getLogger(__name__)

__all__ = ["AutopilotDecayManager"]

#: Artifact statuses the decay scan watches.  Mirrors ``sdm_decay_scan_tool``
#: (ACTIVE + MONITORING) plus DECAYED, so the auto-retire loop can advance
#: decayed factors to the terminal DISABLED state.
_SCAN_STATUSES: tuple[ArtifactStatus, ...] = (
    ArtifactStatus.ACTIVE,
    ArtifactStatus.MONITORING,
    ArtifactStatus.DECAYED,
)

#: Minimum bench readings before a decay signal is trustworthy.
_MIN_BENCH_READINGS: int = 3

#: Universe filter for the autopilot's own factors.
_UNIVERSE = "crypto"


def _default_runtime_root() -> Path:
    """Return the default autopilot runtime root for the notify outbox."""
    return Path(__file__).resolve().parents[2] / "runs" / "autopilot"


class AutopilotDecayManager:
    """Scan crypto artifacts for decay and retire autopilot factors.

    Attributes:
        factor_store: The autopilot factor store (retire + lifecycle).
        strategy_store: SDM strategy store (``None`` disables scanning).
        notifier: IM outbox writer for ``factor_retired`` events.
    """

    def __init__(
        self,
        factor_store: Any,
        strategy_store: Any | None = None,
        notifier: AutopilotNotifier | None = None,
        evaluator: DecayEvaluator | None = None,
    ) -> None:
        """Initialize the decay manager.

        Args:
            factor_store: :class:`FactorStore` instance used to retire
                factors whose artifacts are disabled by the decay scan.
            strategy_store: SDM :class:`StrategyStoreProtocol` instance.
                When ``None`` the process-wide singleton is used (see
                :func:`src.strategy_store._shared.get_store`); if that
                fails, scans become no-ops.
            notifier: IM outbox writer.  Defaults to an
                :class:`AutopilotNotifier` at the autopilot runtime root.
            evaluator: Decay evaluator; defaults to a fresh
                :class:`DecayEvaluator`.
        """
        self.factor_store = factor_store
        self._strategy_store = strategy_store
        # Wire the store back into the factor store so artifact registration,
        # lifecycle sync, and bench recording all target the same SDM
        # instance (the orchestrator builds the store first, then attaches
        # the decay manager).
        if strategy_store is not None and getattr(factor_store, "strategy_store", None) is None:
            factor_store.strategy_store = strategy_store
        self._notifier = notifier or AutopilotNotifier(_default_runtime_root())
        self._evaluator = evaluator or DecayEvaluator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_scan(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Scan crypto artifacts and retire factors that decay to DISABLED.

        Mirrors ``sdm_decay_scan``: for every ACTIVE/MONITORING artifact in
        the ``crypto`` universe, computes baseline/rolling IC + Sharpe from
        the bench history, evaluates the decay signal, and applies the SDM
        state machine (active → monitoring → decayed → disabled).  When an
        artifact is disabled (or already disabled), the corresponding
        autopilot factor is retired and operators are notified.

        Args:
            dry_run: Report verdicts without applying transitions/retires.

        Returns:
            A summary dict with ``scanned``, ``signals``, ``retired``,
            ``transitions``, and ``insufficient_data`` counts.
        """
        store = self._get_store()
        if store is None:
            logger.debug("autopilot decay scan: strategy store unavailable")
            return {"scanned": 0, "signals": {}, "retired": [], "transitions": [], "insufficient_data": 0}

        # Collect candidates once: a transition applied to one artifact must
        # not re-scan it under another status within the same pass, so each
        # scan advances the SDM state machine at most one step.
        targets: list[Any] = []
        for status in _SCAN_STATUSES:
            targets.extend(
                store.list_artifacts(
                    type=None, status=status, universe=_UNIVERSE,
                )
            )

        counts: dict[str, int] = {}
        retired: list[dict[str, Any]] = []
        transitions: list[dict[str, Any]] = []
        insufficient = 0

        for artifact in targets:
            bench_history = list(
                store.get_bench_history(artifact.id, limit=20)
            )
            if len(bench_history) < _MIN_BENCH_READINGS:
                insufficient += 1
                continue

            metrics = compute_decay_metrics(bench_history)
            if not has_decay_inputs(metrics):
                insufficient += 1
                continue

            signal = self._evaluator.evaluate_decay(
                ic_ratio=metrics["ic_ratio"],
                ir=metrics["rolling_ir"],
                ic_positive_ratio=metrics["ic_positive_ratio"],
                sharpe=metrics["rolling_sharpe"],
            )
            counts[signal.value] = counts.get(signal.value, 0) + 1

            decay_history = list(store.get_decay_history(artifact.id, limit=10))
            prior_signals = [
                s.decay_signal for s in reversed(decay_history)
                if s.decay_signal is not None
            ]
            recommended = self._evaluator.should_transition(
                artifact.status, prior_signals + [signal]
            )

            if not dry_run:
                self._record_snapshot(store, artifact.id, metrics, signal, prior_signals + [signal])
            if recommended is not None:
                transitions.append({
                    "artifact_id": artifact.id,
                    "from": artifact.status.value,
                    "to": recommended.value,
                })
                if dry_run:
                    continue
                if recommended is ArtifactStatus.DISABLED:
                    self._retire_factor(artifact.id, signal)
                    retired.append({
                        "artifact_id": artifact.id,
                        "signal": signal.value,
                    })
                else:
                    self._apply_transition(store, artifact.id, recommended, signal)

        return {
            "scanned": sum(counts.values()) + insufficient,
            "signals": counts,
            "retired": retired,
            "transitions": transitions,
            "insufficient_data": insufficient,
        }

    def retire_factor(self, alpha_id: str, reason: str) -> None:
        """Retire an autopilot factor (lifecycle + IM notification).

        Used by the orchestrator's promotion-gate path as well as the
        decay scan, so every retirement flows through one notifier.

        Args:
            alpha_id: The factor identifier.
            reason: Human-readable retirement reason.
        """
        try:
            self.factor_store.advance_lifecycle(alpha_id, FactorLifecycle.RETIRED)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "autopilot decay: failed to retire %s: %s", alpha_id, exc,
            )
            return
        self._notifier.notify(
            "factor_retired",
            f"Factor retired: {alpha_id}",
            reason,
            meta={"alpha_id": alpha_id, "reason": reason},
        )
        logger.warning("autopilot decay: retired %s — %s", alpha_id, reason)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_store(self) -> Any | None:
        """Return the strategy store, resolving the singleton lazily."""
        if self._strategy_store is not None:
            return self._strategy_store
        try:
            from src.strategy_store._shared import get_store

            return get_store()
        except Exception as exc:  # noqa: BLE001
            logger.warning("autopilot decay: strategy store unavailable: %s", exc)
            return None

    def _retire_factor(self, alpha_id: str, signal: Any) -> None:
        """Retire a factor whose artifact reached the disabled state."""
        self.retire_factor(
            alpha_id,
            f"decay scan: {signal.value} signal disabled the factor",
        )

    def _apply_transition(
        self,
        store: Any,
        artifact_id: str,
        recommended: ArtifactStatus,
        signal: Any,
    ) -> None:
        """Apply a non-terminal SDM status transition (best-effort)."""
        try:
            store.update_status(
                artifact_id,
                recommended,
                reason=f"Autopilot decay scan: {signal.value} signal triggered transition",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "autopilot decay: status transition failed for %s: %s",
                artifact_id, exc,
            )

    def _record_snapshot(
        self,
        store: Any,
        artifact_id: str,
        metrics: dict[str, Any],
        signal: Any,
        signals: list[Any],
    ) -> None:
        """Record one decay snapshot (best-effort)."""
        consecutive = 0
        for s in reversed(signals):
            if s.value != "healthy":
                consecutive += 1
            else:
                break
        try:
            store.record_decay_snapshot(
                DecaySnapshot(
                    artifact_id=artifact_id,
                    rolling_ic_mean=metrics["rolling_ic_mean"],
                    rolling_ir=metrics["rolling_ir"],
                    baseline_ic_mean=metrics["baseline_ic_mean"],
                    ic_ratio=metrics["ic_ratio"],
                    decay_signal=signal,
                    consecutive_warnings=consecutive,
                    detail=json.dumps(metrics, ensure_ascii=False),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "autopilot decay: snapshot failed for %s: %s",
                artifact_id, exc,
            )
