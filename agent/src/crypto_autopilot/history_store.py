"""Incremental OKX K-line history store for the crypto autopilot.

The autopilot's live panel only carries ``bar_limit`` bars (~7.5 days of 1h),
which is far too short for statistically meaningful factor evaluation. This
module persists per-symbol OHLCV bars as parquet under
``~/.vibe-trading/data/history/`` and serves long windows to the evaluation
backtest so gates like :class:`OverfitGate` run on months of data instead of
noise-sized windows.

Two sync strategies:

* :meth:`ensure_history` — first-run full fetch (OKX public history-candles
  endpoint, no auth) or incremental merge when the store already covers the
  requested span.
* :meth:`append_latest` — cheap per-tick incremental append of the most
  recent bars so the store stays fresh without re-downloading settled data.

Every write is atomic (same-dir temp + replace) so a crash mid-write can
never corrupt the store; an unreadable file is treated as empty and rebuilt.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

__all__ = ["HistoryStore"]

#: Canonical OHLCV columns carried in the store.
_BAR_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

#: Interval aliases for the OKX loader.
_INTERVAL_ALIASES: dict[str, str] = {
    "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H",
    "1d": "1D", "1w": "1W", "1M": "1M",
}


def _merge_bars(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Concatenate, de-duplicate on index (keep latest), and sort ascending.

    Handles timezone-naive/aware index mismatches by stripping tz so bars
    from different sources line up on the same DatetimeIndex.
    """
    frames = [f for f in (existing, fresh) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=_BAR_COLUMNS)
    merged = pd.concat(frames)
    if merged.index.tz is not None:
        merged.index = merged.index.tz_localize(None)
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()


class HistoryStore:
    """Parquet-backed incremental OHLCV store with window reads.

    Attributes:
        root: Directory holding ``<symbol>_<period>.parquet`` files.
    """

    def __init__(self, root: Path | None = None) -> None:
        """Initialize the store, creating the root directory.

        Args:
            root: Store directory; defaults to
                ``~/.vibe-trading/data/history``.
        """
        self.root = Path(root) if root is not None else (
            Path.home() / ".vibe-trading" / "data" / "history"
        )
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def path_for(self, symbol: str, period: str = "1h") -> Path:
        """Return the parquet path for *symbol* at *period*."""
        safe = symbol.replace("/", "-").upper()
        return self.root / f"{safe}_{period}.parquet"

    def ensure_history(
        self,
        symbol: str,
        period: str = "1h",
        days: int = 365,
    ) -> pd.DataFrame:
        """Guarantee at least *days* of bars for *symbol*; return the frame.

        When the store already covers the span the frame is returned as-is;
        otherwise the missing range is fetched from the OKX public
        history-candles endpoint and merged. Best-effort: any fetch failure
        is logged and the existing (possibly partial) frame is returned so
        the caller degrades gracefully.
        """
        existing = self._load(symbol, period)
        need = days * 24  # hourly bars per day
        if len(existing) >= need:
            return existing
        end = pd.Timestamp.utcnow().tz_localize(None).normalize() + pd.Timedelta(days=1)
        start = end - pd.Timedelta(days=days)
        fetched = self._fetch_range(symbol, period, start, end)
        if fetched is None:
            return existing
        merged = _merge_bars(existing, fetched)
        self._save(merged, symbol, period)
        logger.info(
            "history_store: %s %s ensured (%d bars, %s..%s)",
            symbol, period, len(merged), start.date(), end.date(),
        )
        return merged

    def append_latest(
        self,
        symbols: list[str],
        period: str = "1h",
        limit: int = 200,
    ) -> dict[str, int]:
        """Incrementally append recent bars for each symbol.

        Args:
            symbols: Instrument ids, e.g. ``["BTC-USDT"]``.
            period: Bar size, default ``1h``.
            limit: Bars to request per symbol from the live feed.

        Returns:
            Mapping ``{symbol: bars_appended}``; ``-1`` marks a failed fetch
            (logged, never raised — the loop must not break on data lag).
        """
        from src.crypto_autopilot.market_feed import MarketFeed

        feed = MarketFeed()
        added: dict[str, int] = {}
        for symbol in symbols:
            try:
                existing = self._load(symbol, period)
                fresh = feed.fetch_bars(symbol, period=period, limit=limit)
                merged = _merge_bars(existing, fresh)
                delta = len(merged) - len(existing)
                if delta > 0:
                    self._save(merged, symbol, period)
                added[symbol] = delta
            except Exception as exc:  # noqa: BLE001 — data lag must not kill the loop
                logger.warning("history_store: append %s failed: %s", symbol, exc)
                added[symbol] = -1
        return added

    def get_window(
        self,
        symbol: str,
        period: str = "1h",
        bars: int = 1440,
    ) -> pd.DataFrame:
        """Return the most recent *bars* for *symbol* (ascending)."""
        df = self._load(symbol, period)
        if df.empty:
            return df
        return df.tail(bars)

    def latest_ts(self, symbol: str, period: str = "1h") -> pd.Timestamp | None:
        """Return the newest bar timestamp for *symbol*, or ``None``."""
        df = self._load(symbol, period)
        return df.index[-1] if not df.empty else None

    def get_panel(
        self,
        pairs: list[str],
        period: str = "1h",
        bars: int = 1440,
    ) -> dict[str, pd.DataFrame]:
        """Assemble a wide evaluate panel from the stored history.

        Returns an empty dict when no symbol has data (the caller falls back
        to the live rolling panel).
        """
        from src.crypto_autopilot.panel_builder import PanelBuilder

        per_symbol: dict[str, pd.DataFrame] = {}
        for symbol in pairs:
            df = self.get_window(symbol, period=period, bars=bars)
            if not df.empty:
                per_symbol[symbol] = df
        if not per_symbol:
            return {}
        return PanelBuilder().build_panel(per_symbol)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_range(
        self,
        symbol: str,
        period: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> pd.DataFrame | None:
        """Fetch *symbol* bars over [start, end] from the OKX loader.

        Always prefers the deep ``history-candles`` endpoint: the recent-only
        endpoint caps at ~1440 bars (~60 days of 1h), far below the store's
        one-year span.
        """
        try:
            from backtest.loaders.okx import DataLoader

            interval = _INTERVAL_ALIASES.get(period, period.upper())
            result = DataLoader().fetch(
                codes=[symbol],
                start_date=str(start.date()),
                end_date=str(end.date()),
                interval=interval,
                prefer_history=True,
            )
            return result.get(symbol)
        except Exception as exc:  # noqa: BLE001 — fetch is best-effort
            logger.warning("history_store: fetch %s failed: %s", symbol, exc)
            return None

    def _load(self, symbol: str, period: str) -> pd.DataFrame:
        """Read the parquet file, tolerating absence and corruption."""
        path = self.path_for(symbol, period)
        if not path.exists():
            return pd.DataFrame(columns=_BAR_COLUMNS)
        try:
            df = pd.read_parquet(path)
            if "ts" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
                df = df.set_index("ts")
            df.index = pd.DatetimeIndex(df.index)
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            if not df.index.is_monotonic_increasing:
                df = df.sort_index()
            return df
        except Exception as exc:  # noqa: BLE001 — corrupt store is rebuilt
            logger.warning(
                "history_store: unreadable %s (%s); starting fresh",
                path, exc,
            )
            return pd.DataFrame(columns=_BAR_COLUMNS)

    def _save(self, df: pd.DataFrame, symbol: str, period: str) -> None:
        """Atomically persist *df* (index named ``ts`` for round-trip)."""
        out = df.copy()
        out.index.name = "ts"
        path = self.path_for(symbol, period)
        tmp = path.with_suffix(".parquet.tmp")
        out.to_parquet(tmp)
        tmp.replace(path)
