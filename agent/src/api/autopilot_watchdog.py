"""Autopilot liveness watchdog — detects a dead autopilot and alerts operators.

The autopilot loop writes a heartbeat every tick (:mod:`src.crypto_autopilot.health`)
but nothing consumes it: a crash is only visible as a stale panel until a human
looks. This worker runs inside the API-server process (which launchd keeps
alive), polls the heartbeat every ``poll_interval_s``, and emits outbox
notifications on state transitions:

* **alive → stale** — enqueues an ``autopilot_down`` event so operators are
  alerted while launchd is restarting the process.
* **stale → alive** — enqueues a ``crash_recovered`` event (reuses the
  notifier's existing template) so operators know the loop is back.

The first poll after startup never notifies — a server restart must not be
misread as an autopilot outage (the state machine has no prior observation).

Lifecycle is bound to the API server (``_start_autopilot_watchdog`` /
``_stop_autopilot_watchdog``), mirroring the autopilot notify worker.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from src.crypto_autopilot.health import HealthMonitor
from src.crypto_autopilot.notifier import AutopilotNotifier

logger = logging.getLogger(__name__)

__all__ = [
    "AutopilotWatchdog",
    "_autopilot_watchdog",
    "_start_autopilot_watchdog",
    "_stop_autopilot_watchdog",
]

#: Poll interval for the liveness check.
_POLL_INTERVAL_S = 60.0

#: Heartbeat age beyond which the autopilot is considered down. Generous
#: relative to the tick cadence so a momentarily slow tick is not flagged.
_STALE_AFTER_S = 60

#: Event kind emitted when the loop goes stale.
_DOWN_KIND = "autopilot_down"

#: Event kind emitted when the loop comes back (matches notifier templates).
_RECOVERED_KIND = "crash_recovered"

#: 报警冷却: down/recovered 各自 30 分钟内不重复发 — 代理抖动 1 分钟恢复时,
#: 避免 down+recovered 成对轰炸 (通知噪音, 不是故障).
_COOLDOWN_S = 1800


def _runtime_root() -> Path:
    """Return the autopilot runtime root (``<agent>/runs/autopilot``)."""
    return Path(__file__).resolve().parents[2] / "runs" / "autopilot"


class AutopilotWatchdog:
    """Poll the autopilot heartbeat and notify operators on state changes.

    Attributes:
        runtime_root: Autopilot runtime root holding the heartbeat/state files.
        poll_interval_s: Seconds between liveness polls.
        stale_after_s: Heartbeat age (seconds) that counts as stale.
    """

    def __init__(
        self,
        runtime_root: Path | None = None,
        poll_interval_s: float = _POLL_INTERVAL_S,
        stale_after_s: int = _STALE_AFTER_S,
    ) -> None:
        """Initialize the watchdog.

        Args:
            runtime_root: Autopilot runtime root; defaults to the agent's
                ``runs/autopilot`` directory.
            poll_interval_s: Seconds between liveness polls.
            stale_after_s: Heartbeat age (seconds) that counts as stale.
        """
        root = Path(runtime_root) if runtime_root is not None else _runtime_root()
        self._health: HealthMonitor = HealthMonitor(root)
        self._notifier: AutopilotNotifier = AutopilotNotifier(root)
        self._poll_interval_s: float = max(5.0, poll_interval_s)
        self._stale_after_s: int = max(10, stale_after_s)
        self._was_alive: bool | None = None
        self._task: asyncio.Task | None = None
        self._last_down_at: float = 0.0
        self._last_recovered_at: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the polling loop (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("autopilot watchdog started (poll=%ss, stale=%ss)", self._poll_interval_s, self._stale_after_s)

    async def stop(self) -> None:
        """Stop the polling loop (idempotent)."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("autopilot watchdog stopped")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Poll liveness until cancelled."""
        while True:
            try:
                self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a bad poll must not kill the loop
                logger.warning("autopilot watchdog poll failed: %s", exc, exc_info=True)
            await asyncio.sleep(self._poll_interval_s)

    def _check_once(self) -> None:
        """Evaluate liveness and emit notifications on state transitions.

        The first call only records the initial state (never notifies), so a
        backend restart during an autopilot outage does not re-alert on the
        very next poll.
        """
        alive = not self._health.is_stale(max_age_seconds=self._stale_after_s)
        if self._was_alive is None:
            self._was_alive = alive
            logger.debug("autopilot watchdog initial state: alive=%s", alive)
            return
        now = time.monotonic()
        if alive and not self._was_alive:
            if now - self._last_recovered_at >= _COOLDOWN_S:
                self._notifier.notify(
                    _RECOVERED_KIND,
                    "Autopilot recovered",
                    "Autopilot heartbeat is fresh again after going stale.",
                    meta={"stale_after_s": self._stale_after_s},
                )
                self._last_recovered_at = now
                logger.info("autopilot watchdog: autopilot recovered")
            else:
                logger.info("autopilot watchdog: recovered (cooldown, 不重复通知)")
        elif not alive and self._was_alive:
            if now - self._last_down_at >= _COOLDOWN_S:
                self._notifier.notify(
                    _DOWN_KIND,
                    "Autopilot offline",
                    "Autopilot heartbeat is stale — the loop is down or hung. "
                    "launchd should be restarting it.",
                    meta={"stale_after_s": self._stale_after_s},
                )
                self._last_down_at = now
                logger.warning("autopilot watchdog: autopilot heartbeat stale")
            else:
                logger.info("autopilot watchdog: down (cooldown, 不重复通知)")
        self._was_alive = alive


# ---------------------------------------------------------------------------
# Lifecycle hooks (bound by api_server)
# ---------------------------------------------------------------------------

_autopilot_watchdog: AutopilotWatchdog | None = None


def _start_autopilot_watchdog() -> None:
    """Start the singleton autopilot watchdog."""
    global _autopilot_watchdog
    if _autopilot_watchdog is None:
        _autopilot_watchdog = AutopilotWatchdog()
    _autopilot_watchdog.start()


async def _stop_autopilot_watchdog() -> None:
    """Stop the singleton autopilot watchdog if it was started."""
    watchdog = _autopilot_watchdog
    if watchdog is not None:
        await watchdog.stop()
