"""Panel builder for the crypto_autopilot factor pipeline.

Converts narrow per-symbol OHLCV DataFrames (one DataFrame per symbol with
columns ``[open, high, low, close, volume]``) into the wide
``dict[str, pd.DataFrame]`` panel format expected by the Alpha Zoo
``compute(panel)`` protocol — where each key is a field name (e.g.
``"close"``) and each value is a DataFrame with ``index = trading_date``
and ``columns = symbol``.

The Alpha Zoo compute contract (see :class:`src.factors.base.AlphaCompute`
and :meth:`src.factors.registry.Registry.compute`) expects this wide
format so cross-sectional operators (``rank``, ``zscore``, ``scale``)
can operate across symbols per date row.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from src.crypto_autopilot.market_feed import MarketFeed

logger = logging.getLogger(__name__)

__all__ = ["PanelBuilder"]

#: Canonical OHLCV fields carried from narrow bars to the wide panel.
_PANEL_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


class PanelBuilder:
    """Assemble Alpha Zoo ``compute(panel)`` panels from per-symbol bars.

    The Alpha Zoo protocol expects a panel as ``dict[str, pd.DataFrame]``
    where each key is a field name (``"close"``, ``"funding_rate"``,
    ``"oi"``, …) and each value is a *wide* DataFrame
    (``index = trading_date``, ``columns = symbol``).

    This builder transforms the *narrow* output of :class:`MarketFeed`
    (one DataFrame per symbol, columns = OHLCV fields) into that wide
    format. Symbols with different date coverage are aligned via outer
    join with forward fill so interior gaps don't leave NaN rows that
    would break rolling-window operators.
    """

    def build_panel(
        self,
        bars: dict[str, pd.DataFrame],
    ) -> dict[str, pd.DataFrame]:
        """Convert per-symbol narrow bars into a wide panel.

        For each field in ``open/high/low/close/volume``, the per-symbol
        Series are outer-joined on the union of dates and forward-filled.
        Leading NaNs (dates before a symbol's first bar) are preserved —
        matching the Alpha Zoo NaN policy where warmup periods return NaN
        rather than silently zero-filled.

        Args:
            bars: Mapping ``{symbol: DataFrame}`` where each DataFrame has
                columns ``[open, high, low, close, volume]`` and a
                DatetimeIndex. Empty DataFrames are skipped.

        Returns:
            Wide panel ``{field: DataFrame}`` with ``index = date``,
            ``columns = symbol``. Returns ``{}`` when *bars* is empty or
            all symbols are empty.
        """
        if not bars:
            return {}

        # Build the union of all dates across non-empty symbol frames.
        all_dates: pd.DatetimeIndex | None = None
        for df in bars.values():
            if df is not None and not df.empty:
                idx = pd.DatetimeIndex(df.index)
                all_dates = idx if all_dates is None else all_dates.union(idx)
        if all_dates is None:
            return {}

        panel: dict[str, pd.DataFrame] = {}
        for col in _PANEL_COLUMNS:
            pieces: dict[str, pd.Series] = {}
            for symbol, df in bars.items():
                if df is None or df.empty or col not in df.columns:
                    continue
                pieces[symbol] = df[col]
            if pieces:
                # pd.DataFrame(dict_of_series) auto-aligns on the union of
                # indices (outer join); NaN fills where a symbol has no bar.
                wide = pd.DataFrame(pieces)
                # Forward-fill interior gaps so a symbol missing one day
                # in the middle of its range doesn't create a NaN row that
                # would zero-out rolling-window operators. Leading NaNs
                # (before the symbol's first bar) are preserved by ffill.
                wide = wide.ffill()
                # Ensure all panel DataFrames share the same index.
                wide = wide.reindex(all_dates)
            else:
                wide = pd.DataFrame(index=all_dates)
            panel[col] = wide
        return panel

    def build_crypto_panel(
        self,
        pairs: list[str] | None = None,
        period: str = "1d",
        limit: int = 90,
        feed: MarketFeed | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Convenience: fetch bars via *feed* then build the panel.

        Args:
            pairs: Trading pairs; defaults to the feed's autopilot config
                pairs when ``None``.
            period: Bar size (default ``1d``).
            limit: Bars per pair (default 90).
            feed: :class:`MarketFeed` instance; created with default config
                (paper profile) when ``None``.

        Returns:
            Wide panel ready for :meth:`Registry.compute`.
        """
        if feed is None:
            from src.crypto_autopilot.market_feed import MarketFeed

            feed = MarketFeed()
        bars = feed.fetch_panel(pairs=pairs, period=period, limit=limit)
        return self.build_panel(bars)
