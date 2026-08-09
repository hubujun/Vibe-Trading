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

#: Regex for valid zoo id tokens (lowercase, digits, underscore).
_ZOO_ID_RE: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*$")


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
    ) -> None:
        """Initialise the factor store.

        Args:
            zoo_root: Root directory for the crypto_mined zoo.  Defaults
                to ``<agent>/src/factors/zoo/crypto_mined/``.
            hypotheses_registry: Optional
                :class:`~src.hypotheses.registry.HypothesisRegistry`
                instance for tracking the factor's research lifecycle.
                When ``None``, lifecycle integration is a no-op.
        """
        self.zoo_root = Path(zoo_root) if zoo_root is not None else _default_zoo_root()
        self.hypotheses_registry = hypotheses_registry

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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

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
