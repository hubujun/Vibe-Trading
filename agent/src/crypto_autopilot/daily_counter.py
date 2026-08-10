"""Persisted daily order counter for the autopilot live executor.

The live executor enforces a per-UTC-day order cap via
:func:`src.live.enforcement.check_mandate` (``daily_count``).  That count
must survive process restarts — a 24/7 process that re-reads a zeroed
in-memory counter after a crash would silently bypass
``max_trades_per_day``.  :class:`DailyOrderCounter` stores
``{"date": "<UTC date>", "count": N}`` under the autopilot runtime root
and resets automatically when the UTC date rolls over.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["DailyOrderCounter", "COUNTER_FILENAME"]

#: File name of the persisted counter inside the runtime root.
COUNTER_FILENAME = "daily_orders.json"


class DailyOrderCounter:
    """Persist the number of orders placed on the current UTC day.

    Attributes:
        runtime_root: Directory that holds ``daily_orders.json``.
    """

    def __init__(self, runtime_root: Path) -> None:
        """Initialize the counter backed by ``<runtime_root>/daily_orders.json``.

        Args:
            runtime_root: Autopilot runtime root (same directory family as
                the health monitor's state files).
        """
        self.runtime_root = Path(runtime_root)
        self._path = self.runtime_root / COUNTER_FILENAME

    def count_today(self) -> int:
        """Return the number of orders already placed today (UTC).

        A missing file, a file dated on a previous UTC day, or an
        unreadable/corrupt file all read as zero (the daily cap restarts
        clean). Corruption is logged and treated as zero — fail-open here
        is deliberate: the mandate gate still caps the *next* order, so a
        reset cannot exceed ``max_trades_per_day`` for any single burst.
        """
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if raw.get("date") != _utc_today():
                return 0
            count = int(raw.get("count", 0))
            return max(0, count)
        except FileNotFoundError:
            return 0
        except (ValueError, TypeError, OSError) as exc:
            logger.warning(
                "daily order counter unreadable at %s (%s); counting as 0",
                self._path, exc,
            )
            return 0

    def increment(self) -> int:
        """Increment today's counter and persist it atomically.

        Returns:
            The new count for today (UTC).
        """
        count = self.count_today() + 1
        self._write({"date": _utc_today(), "count": count})
        return count

    def _write(self, payload: dict) -> None:
        """Atomically write the counter via a same-directory temp file."""
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.runtime_root),
            prefix=f".{COUNTER_FILENAME}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


def _utc_today() -> str:
    """Return the current UTC date as ``YYYY-MM-DD``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
