"""Market data feed for the crypto_autopilot pipeline.

Wraps the OKX SDK (:mod:`src.trading.connectors.okx.sdk`) to fetch OHLCV
candlesticks for multiple trading pairs and periods. v1 uses REST polling
(hourly/daily factors don't need sub-second data); a WebSocket streaming
interface is stubbed for v2.

Incremental caching reuses the opt-in ``VIBE_TRADING_DATA_CACHE`` mechanism
from :mod:`backtest.loaders.base`: when enabled, fetched bars are persisted
to parquet and subsequent calls merge fresh bars with the cache so the
history accumulates without re-downloading settled data.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.loaders.base import loader_cache_enabled, loader_cache_root
from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.trading.connectors.okx import sdk as okx_sdk
from src.trading.connectors.okx.sdk import OKXConfig

logger = logging.getLogger(__name__)

__all__ = ["MarketFeed"]

#: Default minimum interval (seconds) between OKX REST requests.
_DEFAULT_MIN_INTERVAL_S: float = 0.2

#: Cache subdirectory for autopilot market data (under ``loader_cache_root()``).
_CACHE_SUBDIR: str = "autopilot"

#: Canonical OHLCV columns carried from OKX bars to the DataFrame.
_BAR_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

#: Sentinel index column name so the DatetimeIndex survives a parquet round-trip.
_CACHE_INDEX_COL: str = "_ts"


class MarketFeed:
    """OKX K-line feed with multi-pair support, incremental cache, and rate protection.

    Paper-vs-live is controlled by the injected :class:`OKXConfig` profile
    (default ``"paper"``). v1 fetches via REST polling; :meth:`stream_bars`
    is a placeholder for the v2 WebSocket upgrade.

    Attributes:
        okx_config: OKX connector config (profile, host, credentials).
        autopilot_config: Autopilot tuning knobs (pairs, bars_per_year, etc.).
        min_interval_s: Minimum seconds between REST requests (rate protection).
    """

    def __init__(
        self,
        okx_config: OKXConfig | None = None,
        autopilot_config: AutopilotConfig | None = None,
        *,
        min_interval_s: float = _DEFAULT_MIN_INTERVAL_S,
    ) -> None:
        """Initialize the feed with injected configs.

        Args:
            okx_config: OKX connector config; defaults to a paper-profile
                config (empty credentials — sufficient for public market data)
                when ``None``.
            autopilot_config: Autopilot config; defaults to
                :func:`load_autopilot_config` when ``None``.
            min_interval_s: Minimum seconds between REST requests for rate
                protection. Set to ``0`` to disable.
        """
        self._okx_config = okx_config or OKXConfig(profile="paper")
        self._autopilot_config = autopilot_config or load_autopilot_config()
        self._min_interval_s = max(0.0, min_interval_s)
        self._last_request_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public sync API
    # ------------------------------------------------------------------

    def fetch_bars(
        self,
        symbol: str,
        period: str = "1d",
        limit: int = 90,
    ) -> pd.DataFrame:
        """Fetch historical OHLCV bars for one symbol.

        Calls :func:`okx_sdk.get_historical_bars` and converts the response
        into a DataFrame with columns ``[open, high, low, close, volume]`` and
        a ``DatetimeIndex`` derived from the OKX ``time`` field (ms → UTC,
        timezone-stripped).

        When the opt-in cache (``VIBE_TRADING_DATA_CACHE``) is enabled, the
        result is persisted to disk and subsequent calls merge fresh bars
        with the cache (incremental append) so history accumulates without
        re-downloading settled bars.

        Args:
            symbol: OKX instrument id, e.g. ``"BTC-USDT"``.
            period: Bar size (``1m/5m/15m/30m/1h/4h/1d/1w``), default ``1d``.
            limit: Number of bars to request from OKX (max 300).

        Returns:
            DataFrame with columns ``[open, high, low, close, volume]``,
            sorted ascending by timestamp, deduplicated. Empty DataFrame
            (with correct columns) when OKX returns no bars.
        """
        cached = self._cache_get(symbol, period)
        fresh = self._fetch_bars_raw(symbol, period, limit)
        if cached is not None and not cached.empty:
            combined = pd.concat([cached, fresh])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()
            result = combined
        else:
            result = fresh
        self._cache_put(symbol, period, result)
        return result

    def fetch_panel(
        self,
        pairs: list[str] | None = None,
        period: str = "1d",
        limit: int = 90,
    ) -> dict[str, pd.DataFrame]:
        """Fetch bars for multiple trading pairs.

        Iterates over *pairs* (or :attr:`autopilot_config.pairs` when ``None``)
        with rate-limited spacing between requests. A per-symbol failure is
        logged and skipped — partial results are returned.

        Args:
            pairs: Trading pairs to fetch; defaults to autopilot config pairs.
            period: Bar size.
            limit: Bars per pair.

        Returns:
            Mapping ``{symbol: DataFrame}`` of OHLCV bars. Symbols that
            failed or returned empty are omitted.
        """
        symbols = list(pairs) if pairs is not None else list(self._autopilot_config.pairs)
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            try:
                df = self.fetch_bars(symbol, period=period, limit=limit)
                if df is not None and not df.empty:
                    result[symbol] = df
            except Exception as exc:  # noqa: BLE001 — per-symbol isolation
                logger.warning("fetch_panel: %s failed: %s", symbol, exc)
        return result

    async def stream_bars(
        self,
        symbol: str,
        period: str = "1d",
    ) -> None:
        """Stream bars via WebSocket (v2 placeholder).

        v1 uses REST polling (factors operate on hourly/daily bars). The
        WebSocket interface will be implemented in v2 for sub-second data
        and real-time signal execution.

        Args:
            symbol: OKX instrument id.
            period: Bar size.

        Raises:
            NotImplementedError: Always — WebSocket upgrade is planned for v2.
        """
        raise NotImplementedError("WebSocket 升级在 v2")

    # ------------------------------------------------------------------
    # Internal: raw fetch + rate limiting
    # ------------------------------------------------------------------

    def _fetch_bars_raw(
        self,
        symbol: str,
        period: str,
        limit: int,
    ) -> pd.DataFrame:
        """Call the OKX SDK and convert the response to a DataFrame.

        Enforces the minimum inter-request interval before calling the SDK.
        """
        self._rate_limit()
        resp = okx_sdk.get_historical_bars(
            symbol,
            config=self._okx_config,
            period=period,
            limit=limit,
        )
        bars: list[dict[str, Any]] = resp.get("bars") or []
        if not bars:
            logger.warning("OKX returned no bars for %s @ %s", symbol, period)
            return pd.DataFrame(columns=list(_BAR_COLUMNS))
        return self._bars_to_dataframe(bars)

    def _rate_limit(self) -> None:
        """Sleep to enforce the minimum interval between REST requests."""
        if self._min_interval_s <= 0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)
        self._last_request_ts = time.monotonic()

    @staticmethod
    def _bars_to_dataframe(bars: list[dict[str, Any]]) -> pd.DataFrame:
        """Convert OKX bar dicts to a typed DataFrame.

        Args:
            bars: List of ``{"time": str(ms), "open", "high", "low", "close",
                "volume", "volume_ccy", "confirm"}`` dicts from the SDK.

        Returns:
            DataFrame with columns ``[open, high, low, close, volume]``,
            DatetimeIndex (UTC, tz-stripped), sorted ascending, deduplicated.
        """
        df = pd.DataFrame(bars)
        # ``time`` arrives as a string of milliseconds; convert to datetime.
        ts = pd.to_numeric(df["time"], errors="coerce")
        index = pd.to_datetime(ts, unit="ms", utc=True).dt.tz_convert(None)
        df.index = pd.DatetimeIndex(index)
        # Coerce OHLCV to float64 (OKX returns strings; Alpha Zoo operators
        # expect float DataFrames so int inference would break downstream).
        for col in _BAR_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
            else:
                df[col] = float("nan")
        df = df[list(_BAR_COLUMNS)]
        # OKX returns newest-first; sort ascending for causal operations.
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df = df.dropna(subset=["open", "high", "low", "close"])
        return df

    # ------------------------------------------------------------------
    # Internal: incremental cache
    # ------------------------------------------------------------------

    def _cache_path(self, symbol: str, period: str) -> Path:
        """Return the parquet cache path for *symbol* + *period*."""
        safe_symbol = symbol.replace("/", "-").upper()
        safe_period = period.replace(" ", "").upper()
        return (
            loader_cache_root()
            / _CACHE_SUBDIR
            / f"{safe_symbol}_{safe_period}.parquet"
        )

    def _cache_get(self, symbol: str, period: str) -> pd.DataFrame | None:
        """Read cached bars for *symbol* + *period*, or ``None`` on miss.

        Cache misses (disabled, file absent, corrupt) are non-fatal and fall
        back to a live fetch.
        """
        if not loader_cache_enabled():
            return None
        path = self._cache_path(symbol, period)
        if not path.is_file():
            return None
        try:
            raw = pd.read_parquet(path)
            if _CACHE_INDEX_COL in raw.columns:
                raw[_CACHE_INDEX_COL] = pd.to_datetime(raw[_CACHE_INDEX_COL])
                raw = raw.set_index(_CACHE_INDEX_COL)
            return raw
        except Exception as exc:  # noqa: BLE001 — cache miss is non-fatal
            logger.debug("cache read failed for %s @ %s: %s", symbol, period, exc)
            return None

    def _cache_put(self, symbol: str, period: str, df: pd.DataFrame) -> None:
        """Write bars to cache (best-effort, non-fatal on error).

        The DatetimeIndex is stored under a sentinel column name so it
        survives the parquet round-trip.
        """
        if not loader_cache_enabled() or df is None or df.empty:
            return
        path = self._cache_path(symbol, period)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            store = df.copy()
            store.index.name = _CACHE_INDEX_COL
            store.reset_index().to_parquet(path, index=False)
        except Exception as exc:  # noqa: BLE001 — cache write is best-effort
            logger.debug("cache write failed for %s @ %s: %s", symbol, period, exc)
