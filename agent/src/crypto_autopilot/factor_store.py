"""Factor persistence + registry reset adapter for mined crypto factors.

:class:`FactorStore` is the persistence layer between a mined
:class:`FactorCandidate` and the Alpha Zoo registry.  It writes the
factor's assembled module source to ``zoo/crypto_mined/<short_id>.py``,
then **critically** calls :func:`reset_default_registry` to clear the
process-wide singleton cache so the new factor is discoverable by
:func:`get_default_registry` on the next access.

The store also integrates with the :class:`HypothesisRegistry` to track
the factor's research lifecycle (exploring → testing → validated →
monitoring → rejected), mirroring the :class:`FactorLifecycle` enum.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from src.crypto_autopilot.factor_miner import _assemble_module_source
from src.crypto_autopilot.types import FactorCandidate, FactorLifecycle
from src.factors.registry import reset_default_registry
from src.strategy_store.models import Artifact, ArtifactStatus, ArtifactType, BenchResult

logger = logging.getLogger(__name__)

__all__ = ["FactorStore"]

#: Mapping from :class:`FactorLifecycle` stages to hypothesis statuses.
#: ``DISCOVERED`` maps to the initial ``"exploring"`` status (set at
#: creation time, not on advancement).  ``RETIRED`` maps to
#: ``"rejected"``.
_LIFECYCLE_TO_HYPOTHESIS_STATUS: dict[FactorLifecycle, str] = {
    FactorLifecycle.BACKTESTED: "testing",
    FactorLifecycle.PAPER_VALIDATED: "validated",
    FactorLifecycle.LIVE_DEPLOYED: "monitoring",
    FactorLifecycle.RETIRED: "rejected",
}

#: Mapping from :class:`FactorLifecycle` stages to strategy-store artifact
#: statuses (the SDM decay monitor watches ACTIVE/MONITORING artifacts and
#: drives them toward DISABLED, which the autopilot maps back to RETIRED).
_LIFECYCLE_TO_ARTIFACT_STATUS: dict[FactorLifecycle, ArtifactStatus] = {
    FactorLifecycle.BACKTESTED: ArtifactStatus.BENCHING,
    FactorLifecycle.PAPER_VALIDATED: ArtifactStatus.ACTIVE,
    FactorLifecycle.LIVE_DEPLOYED: ArtifactStatus.ACTIVE,
    FactorLifecycle.RETIRED: ArtifactStatus.DISABLED,
}

#: Regex for valid zoo id tokens (lowercase, digits, underscore).
_ZOO_ID_RE: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*$")


def _as_float(value: Any) -> float | None:
    """Coerce a metric value to float, returning ``None`` when invalid."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_zoo_root() -> Path:
    """Return the default crypto_mined zoo directory.

    Returns:
        ``<agent>/src/factors/zoo/crypto_mined/``
    """
    return (
        Path(__file__).resolve().parent.parent
        / "factors"
        / "zoo"
        / "crypto_mined"
    )


class FactorStore:
    """Persist mined factors to the Alpha Zoo and manage their lifecycle.

    The store reuses :func:`_assemble_module_source` from
    :mod:`crypto_autopilot.factor_miner` to produce the complete,
    importable module source, writes it to the zoo directory, and resets
    the process-wide registry cache so the new factor is immediately
    discoverable.

    Attributes:
        zoo_root: Root directory for the crypto_mined zoo.
        hypotheses_registry: Optional hypothesis registry for lifecycle
            tracking.
    """

    def __init__(
        self,
        zoo_root: Path | None = None,
        hypotheses_registry: Any = None,
        strategy_store: Any = None,
    ) -> None:
        """Initialise the factor store.

        Args:
            zoo_root: Root directory for the crypto_mined zoo.  Defaults
                to ``<agent>/src/factors/zoo/crypto_mined/``.
            hypotheses_registry: Optional
                :class:`~src.hypotheses.registry.HypothesisRegistry`
                instance for tracking the factor's research lifecycle.
                When ``None``, lifecycle integration is a no-op.
            strategy_store: Optional
                :class:`~src.strategy_store.store.StrategyStoreProtocol`
                instance (SDM side).  When provided, each stored factor is
                registered as a ``factor`` artifact and lifecycle advances
                are mirrored so the SDM decay monitor can watch and
                auto-retire decaying factors.  When ``None``, the SDM
                integration is a no-op.
        """
        self.zoo_root = Path(zoo_root) if zoo_root is not None else _default_zoo_root()
        self.hypotheses_registry = hypotheses_registry
        self.strategy_store = strategy_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, candidate: FactorCandidate) -> Path:
        """Persist a candidate to ``zoo/crypto_mined/<short_id>.py``.

        Reuses the module assembly logic from
        :func:`factor_miner._assemble_module_source`, writes the file,
        and **critically** calls :func:`reset_default_registry` so the
        process-wide singleton cache is cleared and the new factor is
        discoverable by :func:`get_default_registry`.

        If a hypotheses registry is available, a new hypothesis is
        created (or an existing one found) with status ``"exploring"``.

        Args:
            candidate: The validated factor candidate to persist.

        Returns:
            Path to the written ``.py`` file.
        """
        self.zoo_root.mkdir(parents=True, exist_ok=True)

        short_id = self._compute_short_id(candidate.alpha_id)
        out_path = self.zoo_root / f"{short_id}.py"

        # Reuse the assembly logic from FactorMiner.
        source = _assemble_module_source(candidate)
        out_path.write_text(source, encoding="utf-8")

        logger.info(
            "FactorStore: wrote %s → %s", candidate.alpha_id, out_path,
        )

        # CRITICAL: Reset the process-wide registry singleton so the new
        # factor is discoverable.  Without this, get_default_registry()
        # returns a stale cache that doesn't include the new file.
        reset_default_registry()
        logger.debug("FactorStore: reset_default_registry() called")

        # Optionally record the hypothesis lifecycle.
        if self.hypotheses_registry is not None:
            self._record_hypothesis(candidate)

        # Optionally register the factor with the SDM strategy store so the
        # decay monitor can evaluate it once bench history accumulates.
        self._sync_artifact(candidate)

        return out_path

    def advance_lifecycle(
        self,
        alpha_id: str,
        new_stage: FactorLifecycle,
    ) -> None:
        """Advance a factor's lifecycle stage in the hypotheses registry.

        Maps :class:`FactorLifecycle` stages to hypothesis statuses:
            * ``BACKTESTED`` → ``"testing"``
            * ``PAPER_VALIDATED`` → ``"validated"``
            * ``LIVE_DEPLOYED`` → ``"monitoring"``
            * ``RETIRED`` → ``"rejected"``

        Args:
            alpha_id: The factor identifier.
            new_stage: The new lifecycle stage.
        """
        # Mirror the stage into the SDM artifact status so the decay
        # monitor's state machine stays aligned with the autopilot.  This
        # runs even without a hypotheses registry (the SDM side is
        # independent of the research-lifecycle tracking).
        artifact_status = _LIFECYCLE_TO_ARTIFACT_STATUS.get(new_stage)
        if artifact_status is not None:
            self._sync_lifecycle_status(alpha_id, artifact_status)

        if self.hypotheses_registry is None:
            logger.debug(
                "FactorStore: no hypotheses_registry; skipping lifecycle "
                "advance for %s",
                alpha_id,
            )
            return

        target_status = _LIFECYCLE_TO_HYPOTHESIS_STATUS.get(new_stage)
        if target_status is None:
            logger.debug(
                "FactorStore: no hypothesis mapping for stage %s; skipping",
                new_stage.value,
            )
            return

        # Find the hypothesis by alpha_id (stored as title).
        hyp_id = self._find_hypothesis_id(alpha_id)
        if hyp_id is None:
            logger.warning(
                "FactorStore: no hypothesis found for %s; cannot advance "
                "to %s",
                alpha_id,
                new_stage.value,
            )
            return

        try:
            self.hypotheses_registry.update(
                hyp_id,
                status=target_status,
            )
            logger.info(
                "FactorStore: %s hypothesis %s → %s",
                alpha_id,
                hyp_id,
                target_status,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FactorStore: failed to advance hypothesis for %s: %s",
                alpha_id,
                exc,
            )

        # Mirror the stage into the SDM artifact status so the decay
        # monitor's state machine stays aligned with the autopilot.
        artifact_status = _LIFECYCLE_TO_ARTIFACT_STATUS.get(new_stage)
        if artifact_status is not None:
            self._sync_lifecycle_status(alpha_id, artifact_status)

    def record_bench(
        self,
        alpha_id: str,
        metrics: dict[str, Any],
        *,
        bench_type: str = "backtest",
    ) -> None:
        """Record a backtest/paper bench result for the SDM decay monitor.

        Maps the autopilot's metric dict (``ic_mean``, ``ic_std``, ``ic_ir``,
        ``ic_positive_ratio``, ``ic_t_stat``, ``sharpe``, …) into a
        :class:`BenchResult` for the factor's strategy-store artifact.  Any
        failure is logged and swallowed — decay tracking must never block
        the trading loop.

        Args:
            alpha_id: The factor identifier.
            metrics: Metric dict produced by the screen/backtest/paper
                phases (unknown keys are ignored).
            bench_type: Label for the bench record, e.g. ``"backtest"``
                or ``"paper"``.
        """
        store = self.strategy_store
        if store is None:
            return
        try:
            store.record_bench(
                BenchResult(
                    artifact_id=alpha_id,
                    bench_type=bench_type,
                    ic_mean=_as_float(metrics.get("ic_mean")),
                    ic_std=_as_float(metrics.get("ic_std")),
                    ir=_as_float(metrics.get("ic_ir")) or _as_float(metrics.get("ir")),
                    ic_positive_ratio=_as_float(metrics.get("ic_positive_ratio")),
                    t_stat=_as_float(metrics.get("ic_t_stat")) or _as_float(metrics.get("t_stat")),
                    sharpe=_as_float(metrics.get("sharpe")),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FactorStore: failed to record bench for %s: %s",
                alpha_id,
                exc,
            )

    def list_factors(self) -> list[str]:
        """List all alpha_ids in the crypto_mined zoo.

        Scans the zoo directory for ``.py`` files (excluding ``__init__``
        and underscore-prefixed files) and returns their stems as
        alpha_ids.

        Returns:
            Sorted list of alpha_id strings (file stems).
        """
        if not self.zoo_root.is_dir():
            return []

        ids: list[str] = []
        for py_file in sorted(self.zoo_root.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            ids.append(py_file.stem)
        return ids

    def list_factors_with_meta(self) -> list[dict[str, Any]]:
        """List zoo factors enriched with their ``__alpha_meta__`` details.

        Same scan as :meth:`list_factors`, but each entry is a dict with
        the alpha_id plus the factor's nickname, theme, formula (LaTeX),
        universe, frequency, decay horizon, warmup and notes. Metadata is
        AST-extracted (no import) via :func:`load_alpha_meta_from_py`;
        factors whose metadata fails to parse are included with
        ``meta_ok=False`` rather than dropped, so the dashboard always
        shows the full inventory.

        Returns:
            Sorted list of ``{"alpha_id": str, "meta": {...} | None,
            "meta_ok": bool}`` dicts.
        """
        if not self.zoo_root.is_dir():
            return []

        from src.factors.registry import load_alpha_meta_from_py

        factors: list[dict[str, Any]] = []
        for py_file in sorted(self.zoo_root.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            entry: dict[str, Any] = {"alpha_id": py_file.stem, "meta": None, "meta_ok": False}
            try:
                meta = load_alpha_meta_from_py(py_file)
                entry["meta"] = meta.model_dump()
                entry["meta_ok"] = True
            except Exception as exc:  # noqa: BLE001 — best-effort enrichment
                logger.warning("FactorStore: meta parse failed for %s: %s", py_file.name, exc)
            factors.append(entry)
        return factors

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sync_artifact(self, candidate: FactorCandidate) -> None:
        """Register (or refresh) the factor as an SDM strategy-store artifact.

        Best-effort: any failure is logged and swallowed so SDM sync can
        never block persistence of a mined factor.

        Args:
            candidate: The factor candidate being stored.
        """
        store = self.strategy_store
        if store is None:
            return
        try:
            meta = candidate.meta.get("alpha_meta", {}) if isinstance(candidate.meta, dict) else {}
            existing = store.get_artifact(candidate.alpha_id)
            if existing is not None:
                logger.debug(
                    "FactorStore: artifact %s already registered; refreshing",
                    candidate.alpha_id,
                )
                return
            store.register_artifact(
                Artifact(
                    id=candidate.alpha_id,
                    type=ArtifactType.FACTOR,
                    name=candidate.alpha_id,
                    universe="crypto",
                    status=ArtifactStatus.CREATED,
                    theme=tuple(meta.get("theme", [])),
                    decay_horizon=int(meta.get("decay_horizon", 20) or 20),
                    signal_definition=meta.get("notes", ""),
                    source_paper="crypto_autopilot_mined",
                    signal_engine_path=str(self.zoo_root),
                )
            )
            logger.info(
                "FactorStore: registered SDM artifact %s (crypto)",
                candidate.alpha_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FactorStore: SDM artifact registration failed for %s: %s",
                candidate.alpha_id,
                exc,
            )

    def _sync_lifecycle_status(
        self,
        alpha_id: str,
        status: ArtifactStatus,
    ) -> None:
        """Mirror a lifecycle advance into the SDM artifact status.

        ``RETIRED`` maps to ``DISABLED`` (the terminal decay state) so the
        decay monitor never resurrects a retired factor.  Best-effort.

        Args:
            alpha_id: The factor identifier.
            status: The strategy-store status to apply.
        """
        store = self.strategy_store
        if store is None:
            return
        try:
            if store.get_artifact(alpha_id) is None:
                logger.debug(
                    "FactorStore: no SDM artifact for %s; skipping status sync",
                    alpha_id,
                )
                return
            reason = (
                "factor lifecycle retired by autopilot"
                if status is ArtifactStatus.DISABLED
                else f"factor lifecycle advanced to {status.value}"
            )
            store.update_status(alpha_id, status, reason=reason)
            logger.info(
                "FactorStore: SDM artifact %s → %s",
                alpha_id,
                status.value,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FactorStore: SDM status sync failed for %s: %s",
                alpha_id,
                exc,
            )

    @staticmethod
    def _compute_short_id(alpha_id: str) -> str:
        """Compute the file stem from an alpha_id.

        Mirrors the logic in :meth:`FactorMiner.write_factor`:
        strips the ``crypto_mined_`` prefix when present, and sanitises
        the result to match ``^[a-z][a-z0-9_]*$``.

        Args:
            alpha_id: The full alpha identifier.

        Returns:
            A valid zoo file stem.
        """
        short_id = alpha_id
        if short_id.startswith("crypto_mined_"):
            short_id = short_id[len("crypto_mined_"):]
        if not _ZOO_ID_RE.fullmatch(short_id):
            short_id = alpha_id.replace("crypto_mined_", "cm_")
            short_id = re.sub(r"[^a-z0-9_]", "_", short_id)
        return short_id

    def _record_hypothesis(self, candidate: FactorCandidate) -> None:
        """Create or find a hypothesis for the candidate.

        If a hypothesis with the alpha_id as title already exists, it is
        reused; otherwise a new one is created with status
        ``"exploring"``.

        Args:
            candidate: The factor candidate being stored.
        """
        reg = self.hypotheses_registry
        try:
            # Search for an existing hypothesis with this alpha_id.
            existing = reg.search(query=candidate.alpha_id, limit=10)
            for hyp in existing:
                if hyp.title == candidate.alpha_id:
                    logger.debug(
                        "FactorStore: found existing hypothesis %s for %s",
                        hyp.hypothesis_id,
                        candidate.alpha_id,
                    )
                    return

            # Create a new hypothesis.
            meta = candidate.meta.get("alpha_meta", {})
            theme = ", ".join(meta.get("theme", [])) or "uncategorised"
            reg.create(
                title=candidate.alpha_id,
                thesis=(
                    f"Mined crypto factor (theme: {theme}). "
                    f"Source: {candidate.meta.get('model', 'unknown')}"
                ),
                status="exploring",
                universe="crypto",
                signal_definition=meta.get("notes", ""),
                data_sources=["okx"],
                skills=["crypto_autopilot"],
            )
            logger.info(
                "FactorStore: created hypothesis for %s",
                candidate.alpha_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FactorStore: hypothesis recording failed for %s: %s",
                candidate.alpha_id,
                exc,
            )

    def _find_hypothesis_id(self, alpha_id: str) -> str | None:
        """Find a hypothesis_id by alpha_id (used as title).

        Args:
            alpha_id: The factor identifier.

        Returns:
            The hypothesis_id, or ``None`` if not found.
        """
        if self.hypotheses_registry is None:
            return None
        try:
            results = self.hypotheses_registry.search(query=alpha_id, limit=10)
            for hyp in results:
                if hyp.title == alpha_id:
                    return hyp.hypothesis_id
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "FactorStore: hypothesis search failed for %s: %s",
                alpha_id,
                exc,
            )
        return None
