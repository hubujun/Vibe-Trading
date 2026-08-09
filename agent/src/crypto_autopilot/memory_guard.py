"""Memory-defence for the 24/7 crypto_autopilot process.

A process that runs forever accumulates objects: historical K-line frames,
intermediate factor metrics, and transient allocations from LLM/mining passes.
Left unchecked this drifts toward an OOM kill that looks exactly like a crash
to :mod:`src.crypto_autopilot.health` — a silent restart that loses the
in-memory working set.

This module is the defence layer:

1. :meth:`MemoryGuard.maybe_gc` — periodic ``gc.collect()`` every *N* ticks so
   reference cycles and orphaned factor objects are reclaimed before they
   compound.

2. :meth:`MemoryGuard.trim_window` — sliding-window trimming: only the most
   recent *N* bars of K-line data stay in memory; older history is already
   persisted (by :mod:`src.crypto_autopilot.health` + the data cache), so the
   in-memory footprint is bounded and flat over time.

3. :meth:`MemoryGuard.check_memory` — RSS reporting via ``resource.getrusage``
   plus a tracemalloc view when started, with an over-threshold flag for the
   kill-switch / alert path.

4. :meth:`MemoryGuard.start_tracemalloc` /
   :meth:`MemoryGuard.snapshot_top` — tracemalloc instrumentation for top-N
   allocation sites, used during bring-up / diagnosis rather than every tick.

The guiding principle: a 24/7 process keeps only a *sliding window* of data in
memory; history is durable on disk, never accumulated indefinitely in RAM.
"""

from __future__ import annotations

import gc
import logging
import resource
import sys
import tracemalloc
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["MemoryGuard"]


class MemoryGuard:
    """Memory-defence knobs for a long-running autopilot process.

    Keeps the resident footprint bounded and observable across an unbounded
    run: periodic GC, sliding-window data trimming, and optional tracemalloc
    instrumentation. None of these are safety mechanisms — they protect
    *availability* (no OOM-kill surprise restart), not correctness.

    Attributes:
        max_history_bars: Default sliding-window length (bars) used when a
            caller does not pass an explicit size to :meth:`trim_window`.
        gc_interval_ticks: A ``gc.collect()`` runs every this-many ticks.
        tracemalloc_threshold_mb: RSS above which :meth:`check_memory` flags
            ``exceeded=True`` so the caller can trip a kill switch / alert.
    """

    def __init__(
        self,
        max_history_bars: int = 5000,
        gc_interval_ticks: int = 100,
        tracemalloc_threshold_mb: float = 2048,
    ) -> None:
        """Initialize the memory guard.

        Args:
            max_history_bars: Default sliding-window size (in K-line bars) for
                :meth:`trim_window` when no explicit ``max_bars`` is passed.
            gc_interval_ticks: How many ticks elapse between ``gc.collect()``
                calls. ``0`` or negative disables periodic GC.
            tracemalloc_threshold_mb: RSS threshold (MB) above which
                :meth:`check_memory` reports ``exceeded=True``.
        """
        self.max_history_bars = max_history_bars
        self.gc_interval_ticks = gc_interval_ticks
        self.tracemalloc_threshold_mb = tracemalloc_threshold_mb

    # ------------------------------------------------------------------
    # Periodic garbage collection
    # ------------------------------------------------------------------

    def maybe_gc(self, tick_count: int) -> None:
        """Run ``gc.collect()`` every ``gc_interval_ticks`` ticks.

        A 24/7 loop creates many short-lived factor / metric objects per tick;
        periodic collection reclaims reference cycles before they compound into
        a steady RSS creep. The tick count is caller-supplied (the pipeline
        owns the canonical counter) so the guard stays stateless about phase.

        Args:
            tick_count: The pipeline's current tick counter (monotonic since
                boot). A GC fires when this is a positive multiple of
                ``gc_interval_ticks``.
        """
        if self.gc_interval_ticks <= 0:
            return
        if tick_count > 0 and tick_count % self.gc_interval_ticks == 0:
            collected = gc.collect()
            logger.debug(
                "gc.collect() reclaimed %d objects at tick %d",
                collected,
                tick_count,
            )

    # ------------------------------------------------------------------
    # Memory reporting
    # ------------------------------------------------------------------

    def check_memory(self) -> dict[str, Any]:
        """Report the process's current memory usage and threshold status.

        Uses :func:`resource.getrusage` for the OS-level resident set size and,
        when tracemalloc is running, the traced allocation total. The
        ``exceeded`` flag is the kill-switch / alert hook for the caller.

        ``ru_maxrss`` reports the *peak* resident set size so far (the best
        portable signal from :mod:`resource` — a true instantaneous RSS needs
        ``/proc`` or ``psutil``), so ``rss_mb`` is monotonic by design.

        Returns:
            A dict with keys: ``rss_mb`` (peak RSS so far, in MB),
            ``tracemalloc_mb`` (traced current allocations in MB, ``0.0`` when
            tracemalloc is not running), ``tracemalloc_tracing`` (bool),
            ``exceeded`` (``True`` when ``rss_mb`` exceeds
            ``tracemalloc_threshold_mb``).
        """
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss: kilobytes on Linux, bytes on macOS/BSD — normalise to MB.
        if sys.platform == "darwin":
            rss_mb = usage.ru_maxrss / (1024 * 1024)
        else:
            rss_mb = usage.ru_maxrss / 1024

        tracemalloc_mb = 0.0
        tracing = tracemalloc.is_tracing()
        if tracing:
            current, _peak = tracemalloc.get_traced_memory()
            tracemalloc_mb = current / (1024 * 1024)

        return {
            "rss_mb": round(rss_mb, 2),
            "tracemalloc_mb": round(tracemalloc_mb, 2),
            "tracemalloc_tracing": tracing,
            "exceeded": rss_mb > self.tracemalloc_threshold_mb,
        }

    # ------------------------------------------------------------------
    # Sliding-window trimming
    # ------------------------------------------------------------------

    def trim_window(self, data: dict, max_bars: int) -> dict:
        """Return a copy of *data* with each series trimmed to *max_bars*.

        The 24/7 loop must not accumulate unbounded history in memory: older
        K-line history is already durable on disk (via the data cache and
        :mod:`src.crypto_autopilot.health`), so the in-memory working set is
        kept to a flat sliding window.

        Trims values duck-typed by shape:

        * pandas-DataFrame-like (has ``iloc``) → ``.iloc[-max_bars:]``
        * list / tuple → slice ``[-max_bars:]``
        * other → left untouched

        Args:
            data: A dict whose values are time-ordered series (DataFrames,
                lists, etc.) keyed by symbol or feature name.
            max_bars: Maximum number of most-recent rows/elements to keep per
                value. ``0`` or negative returns the data untouched.

        Returns:
            A new dict with the same keys and trimmed values. The input is not
            mutated.
        """
        if max_bars <= 0:
            return dict(data)
        trimmed: dict[str, Any] = {}
        for key, value in data.items():
            trimmed[key] = self._trim_one(value, max_bars)
        return trimmed

    @staticmethod
    def _trim_one(value: Any, max_bars: int) -> Any:
        """Trim a single series to its last *max_bars* elements."""
        # DataFrame-like (pandas) — avoid a hard pandas import; duck-type iloc.
        iloc = getattr(value, "iloc", None)
        if iloc is not None:
            try:
                length = len(value)
            except TypeError:
                return value
            if length > max_bars:
                return iloc[-max_bars:]
            return value
        if isinstance(value, (list, tuple)):
            if len(value) > max_bars:
                return value[-max_bars:]
            return value
        return value

    # ------------------------------------------------------------------
    # tracemalloc instrumentation
    # ------------------------------------------------------------------

    def start_tracemalloc(self) -> None:
        """Start tracemalloc if it is not already running.

        Idempotent: calling twice is a no-op. tracemalloc adds ~5-10% overhead,
        so it is intended for bring-up / diagnosis, not every-tick production.
        """
        if not tracemalloc.is_tracing():
            tracemalloc.start()
            logger.info("tracemalloc started")

    def snapshot_top(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the top-N memory allocation sites by current size.

        Requires tracemalloc to be running (call :meth:`start_tracemalloc`
        first); when not running, returns an empty list rather than starting it
        implicitly (tracemalloc is opt-in to avoid per-tick overhead).

        Args:
            n: Maximum number of allocation sites to return.

        Returns:
            A list of dicts (highest current size first), each with keys:
            ``filename``, ``lineno``, ``size_bytes``, ``count``. Empty when
            tracemalloc is not tracing.
        """
        if not tracemalloc.is_tracing():
            return []
        snapshot = tracemalloc.take_snapshot()
        stats = snapshot.statistics("lineno")[:n]
        result: list[dict[str, Any]] = []
        for stat in stats:
            frame = stat.traceback[0]
            result.append(
                {
                    "filename": frame.filename,
                    "lineno": frame.lineno,
                    "size_bytes": stat.size,
                    "count": stat.count,
                }
            )
        return result
