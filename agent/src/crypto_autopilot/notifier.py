"""Best-effort IM notification outbox for the crypto autopilot.

The autopilot runs as its own CLI process while the IM channels
(:mod:`src.channels`) live inside the API-server process, so there is no
shared in-memory bus. Instead the notifier writes a small JSON *outbox
file* per event under ``<runtime_root>/notifications/``; the API server's
:mod:`src.api.autopilot_notify` worker polls that directory and relays each
file to the configured chat channels. This mirrors the filesystem-contract
style of the HALT sentinel and the HealthMonitor heartbeat/state files.

The write is best-effort: any failure is logged and swallowed so a
notification problem can never block a trading decision (mirroring
:meth:`src.crypto_autopilot.health.HealthMonitor.write_heartbeat`).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AutopilotNotifier", "NOTIFY_DIR", "NOTIFY_KINDS"]

#: Sub-directory under the runtime root holding pending notification files.
NOTIFY_DIR = "notifications"

#: Recognized event kinds (each maps to a distinct message template).
NOTIFY_KINDS: tuple[str, ...] = (
    "order_filled",
    "halt_tripped",
    "factor_promoted",
    "factor_retired",
    "crash_recovered",
    "autopilot_down",
    "data_stale",
)


class AutopilotNotifier:
    """Persist autopilot events as outbox files for the IM notify worker.

    Attributes:
        runtime_root: Directory that holds the ``notifications/`` outbox.
    """

    def __init__(self, runtime_root: Path) -> None:
        """Initialize the notifier backed by ``<runtime_root>/notifications``.

        Args:
            runtime_root: Autopilot runtime root (same directory family as
                the health monitor's state files).
        """
        self.runtime_root = Path(runtime_root)
        self._outbox = self.runtime_root / NOTIFY_DIR

    def notify(
        self,
        kind: str,
        title: str,
        body: str,
        *,
        meta: dict[str, Any] | None = None,
    ) -> Path | None:
        """Enqueue one notification event, returning its file path.

        Writes ``<runtime_root>/notifications/<ts>_<uuid>.json`` atomically
        (same-directory temp file + ``os.replace``). Unknown *kind* values
        are still persisted (the worker renders a generic template), so new
        event types never break the outbox.

        Args:
            kind: One of :data:`NOTIFY_KINDS` (or a future extension).
            title: Short headline shown as the message title.
            body: Detail text shown under the title.
            meta: Optional structured context (symbol, price, reason, …).

        Returns:
            The written file path, or ``None`` when the write failed
            (best-effort — never raises).
        """
        payload = {
            "id": uuid.uuid4().hex,
            "kind": str(kind),
            "title": str(title),
            "body": str(body),
            "created_at": _utc_now_iso(),
            "meta": dict(meta or {}),
        }
        try:
            self._outbox.mkdir(parents=True, exist_ok=True, mode=0o700)
            ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._outbox),
                prefix=f"{ts}_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                final = self._outbox / f"{ts}_{payload['id']}.json"
                os.replace(tmp_name, final)
                return final
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except Exception as exc:  # noqa: BLE001 — notifications must never break trading
            logger.warning(
                "autopilot notification (%s) dropped: %s",
                kind, exc,
            )
            return None


def _utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
