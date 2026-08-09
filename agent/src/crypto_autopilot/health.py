"""Health & crash-recovery infrastructure for the 24/7 crypto_autopilot loop.

A process that trades around the clock must be *detectably* alive and
*resumable* after an ungraceful exit (SIGKILL, OOM, host crash, power loss).
This module provides two complementary guarantees:

1. **Heartbeat** — :meth:`HealthMonitor.write_heartbeat` records a fresh
   timestamp every tick so an external observer (or a watchdog) can tell the
   live process from a zombie via :meth:`HealthMonitor.is_stale`. The write is
   atomic (same-dir temp + ``os.replace``), mirroring
   :func:`src.live.runtime.liveness.write_heartbeat`, and *best-effort*: a
   failed write is logged and swallowed so a liveness-signal problem can never
   block a trading decision (mirroring ``LiveRunner._write_heartbeat``).

2. **Pipeline-state durability** — :meth:`HealthMonitor.save_pipeline_state`
   atomically persists the :class:`~src.crypto_autopilot.types.PipelineState`
   (temp + ``fsync`` + ``os.replace``, the crash-safe pattern from
   :mod:`src.core.state`). On reboot :meth:`load_pipeline_state` reads the
   last committed snapshot so the loop can resume-via-recompute from a known
   phase instead of starting blind.

Recovery follows the *resume-via-recompute* philosophy of
:class:`src.live.runtime.runner.LiveRunner`: there is no mid-task checkpoint,
only a phase marker. A restart re-fetches market data and recomputes the
current phase's inputs from the persisted phase, so the only durable artifact
is the phase/counter snapshot written here.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.crypto_autopilot.types import PipelinePhase, PipelineState

logger = logging.getLogger(__name__)

__all__ = ["HealthMonitor"]

#: Sub-directory under the runtime root for autopilot liveness/state files.
_AUTOPILOT_DIR = "autopilot"

#: Heartbeat file name inside the autopilot directory.
_HEARTBEAT_FILE = "heartbeat.json"

#: Pipeline-state file name inside the autopilot directory.
_STATE_FILE = "state.json"


def _now_ms() -> int:
    """Return the current wall-clock time in epoch milliseconds.

    Returns:
        Milliseconds since the Unix epoch.
    """
    return int(time.time() * 1000)


class HealthMonitor:
    """24/7 heartbeat writer and crash-safe pipeline-state store.

    The monitor owns two artifacts under ``<runtime_root>/autopilot/``:
    ``heartbeat.json`` (a fresh timestamp written every tick) and
    ``state.json`` (the last committed :class:`PipelineState`). Together they
    let a watchdog detect a dead process and let a restart resume the loop
    without starting blind.

    Heartbeat writes are best-effort — a failure is logged and swallowed so a
    liveness problem can never block a trading decision (mirroring
    ``LiveRunner._write_heartbeat``). State writes are crash-safe: they use a
    same-directory temp file, ``fsync``, and ``os.replace`` so a crash mid-write
    can never leave a truncated ``state.json`` (the pattern from
    :mod:`src.core.state` extended with an atomic rename).

    Attributes:
        runtime_root: The runtime root directory; liveness/state files live
            under ``<runtime_root>/autopilot/``.
        runner_id: Stable identifier recorded in the heartbeat payload.
    """

    def __init__(self, runtime_root: Path, runner_id: str = "crypto-autopilot") -> None:
        """Initialize the health monitor.

        Args:
            runtime_root: Runtime root directory (e.g. the live root). The
                ``autopilot/`` sub-directory is created lazily on first write.
            runner_id: Stable runner identifier recorded in the heartbeat so
                an observer can correlate the liveness file with a process.
        """
        self.runtime_root = Path(runtime_root)
        self.runner_id = runner_id
        self._dir = self.runtime_root / _AUTOPILOT_DIR
        self._heartbeat_path = self._dir / _HEARTBEAT_FILE
        self._state_path = self._dir / _STATE_FILE

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def write_heartbeat(self, now_ms: int) -> None:
        """Atomically record a fresh heartbeat timestamp for this tick.

        Writes a small JSON payload (``runner_id``, ``timestamp_ms``, ``pid``)
        to ``<runtime_root>/autopilot/heartbeat.json`` using a same-directory
        temp file + ``os.replace`` so a concurrent :meth:`is_stale` read can
        never see a torn payload.

        The write is *best-effort*: any failure (disk full, permissions, etc.)
        is logged as a warning and swallowed so a liveness-signal problem can
        never block the trading decision — mirroring
        ``LiveRunner._write_heartbeat`` (runner.py). A missed heartbeat only
        makes the loop *look* stale, which the caller handles safely.

        Args:
            now_ms: The tick timestamp in epoch milliseconds to record.
        """
        try:
            payload = {
                "runner_id": self.runner_id,
                "timestamp_ms": int(now_ms),
                "pid": os.getpid(),
            }
            self._atomic_write_json(self._heartbeat_path, payload)
        except Exception:  # noqa: BLE001 — liveness must not break trading
            logger.warning(
                "failed to write heartbeat for %s",
                self.runner_id,
                exc_info=True,
            )

    def is_stale(self, max_age_seconds: int = 300) -> bool:
        """Report whether the heartbeat is older than *max_age_seconds*.

        A missing or unreadable heartbeat is treated as stale (fail-closed),
        mirroring :func:`src.live.runtime.liveness.is_runner_alive` which reads
        "no signal" as not-alive.

        Args:
            max_age_seconds: Maximum heartbeat age in seconds before it is
                considered stale. Defaults to 300 (5 minutes) — generous
                relative to a tick so a momentarily slow tick is never
                false-flagged.

        Returns:
            ``True`` if no heartbeat exists or it is older than
            ``max_age_seconds``; ``False`` if it is fresh.
        """
        ts = self._read_heartbeat_ts()
        if ts is None:
            return True
        return (_now_ms() - ts) > max_age_seconds * 1000

    # ------------------------------------------------------------------
    # Pipeline-state durability
    # ------------------------------------------------------------------

    def save_pipeline_state(self, state: PipelineState) -> None:
        """Atomically persist *state* so a crash can never corrupt it.

        Writes JSON to a same-directory temp file, ``fsync`` s it, then
        ``os.replace`` s it onto ``<runtime_root>/autopilot/state.json`` — the
        crash-safe pattern from :mod:`src.core.state`. A crash before the rename
        leaves the old state intact; ``os.replace`` is atomic on POSIX, so a
        crash during the rename leaves either the old file or the complete new
        one — never a half-written truncation.

        Unlike :meth:`write_heartbeat`, a failure here propagates (the caller
        decides whether a missed state commit aborts the tick): a corrupt phase
        marker on resume is worse than a raised OSError now.

        Args:
            state: The pipeline snapshot to commit.
        """
        payload = _state_to_dict(state)
        self._atomic_write_json(self._state_path, payload)

    def load_pipeline_state(self) -> PipelineState | None:
        """Read the last committed :class:`PipelineState`, or ``None``.

        Used on reboot to resume-via-recompute: the loop re-fetches market data
        and recomputes the current phase's inputs from the persisted phase
        marker, so the only durable artifact is the snapshot written by
        :meth:`save_pipeline_state`.

        Returns:
            The last committed pipeline state, or ``None`` when no state file
            exists or it is unreadable / corrupt (fail-closed: a corrupt state
            is treated as "start fresh" rather than risking a wrong resume).
        """
        if not self._state_path.exists():
            return None
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            return _state_from_dict(json.loads(raw))
        except (OSError, ValueError, TypeError, KeyError):
            logger.warning(
                "corrupt or unreadable pipeline state at %s; starting fresh",
                self._state_path,
                exc_info=True,
            )
            return None

    # ------------------------------------------------------------------
    # Composite liveness
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        """Report whether the loop looks alive (fresh heartbeat *and* state).

        Composite of :meth:`is_stale` and the existence of a state file: a live
        process heartbeats every tick *and* persists its phase. Either signal
        missing means the process is not considered alive.

        Returns:
            ``True`` if the heartbeat is fresh and a state file exists;
            ``False`` otherwise.
        """
        if self.is_stale():
            return False
        return self._state_path.exists()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_heartbeat_ts(self) -> int | None:
        """Return the recorded heartbeat timestamp in ms, or ``None``.

        Treats a missing, unreadable, or malformed heartbeat as "no signal"
        (fail-closed), mirroring
        :func:`src.live.runtime.liveness.last_tick`.
        """
        if not self._heartbeat_path.exists():
            return None
        try:
            raw = self._heartbeat_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return int(data["timestamp_ms"])
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _atomic_write_json(self, path: Path, payload: Any) -> None:
        """Write *payload* as JSON atomically: temp + fsync + replace.

        Creates the parent directory first, writes to a same-directory temp
        file, ``fsync`` s it (crash-safe), then ``os.replace`` s it onto *path*
        so a concurrent reader never sees a torn or truncated file. This is the
        pattern from :mod:`src.core.state` extended with an atomic rename.
        """
        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def _state_to_dict(state: PipelineState) -> dict[str, Any]:
    """Serialize a :class:`PipelineState` to a JSON-able dict.

    Datetimes become ISO-8601 strings and the phase enum becomes its string
    value so the payload survives a JSON round-trip.
    """
    return {
        "phase": state.phase.value,
        "active_factor_id": state.active_factor_id,
        "last_tick_at": state.last_tick_at.isoformat() if state.last_tick_at else None,
        "tick_count": state.tick_count,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }


def _state_from_dict(data: dict[str, Any]) -> PipelineState:
    """Reconstruct a :class:`PipelineState` from a deserialized dict.

    Inverse of :func:`_state_to_dict`. Datetimes parse back as timezone-aware
    UTC (ISO-8601 round-trip).

    Raises:
        KeyError: If a required key is absent (the caller treats this as
            "start fresh" via :meth:`HealthMonitor.load_pipeline_state`).
        ValueError: If ``phase`` is not a valid :class:`PipelinePhase`.
    """
    last_tick = data["last_tick_at"]
    updated = data["updated_at"]
    return PipelineState(
        phase=PipelinePhase(data["phase"]),
        active_factor_id=data["active_factor_id"],
        last_tick_at=datetime.fromisoformat(last_tick) if last_tick else None,
        tick_count=data["tick_count"],
        updated_at=datetime.fromisoformat(updated) if updated else None,
    )
