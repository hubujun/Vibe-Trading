"""Paper-trading engine backed by the OKX demo (paper) account.

Executes real market orders against OKX's simulated-trading environment
(``flag="1"``) while maintaining a local position book so the autopilot
pipeline can inspect open exposure, compute daily P&L, and derive
risk metrics (rolling Sharpe, max drawdown) without querying the broker
for every read.

Spot trading uses ``tdMode="cash"`` — no leverage, no liquidation risk —
which is ideal for validating mined factors with small capital before
promotion to the live profile.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np

from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.crypto_autopilot.market_feed import MarketFeed
from src.crypto_autopilot.types import PaperPosition
from src.trading.connectors.okx import sdk as okx_sdk
from src.trading.connectors.okx.sdk import OKXConfig

logger = logging.getLogger(__name__)

__all__ = ["PaperEngine"]


class PaperEngine:
    """OKX demo-account paper-trading engine.

    Maintains a local position book derived from order fills so risk
    metrics (P&L, Sharpe, drawdown) can be computed without additional
    broker round-trips.  The actual order execution is delegated entirely
    to :func:`okx_sdk.place_order` under a paper-profile
    :class:`OKXConfig` (``flag="1"`` → demo environment).

    Attributes:
        config: Autopilot tuning knobs (max notional, kill-loss, etc.).
        okx_config: OKX connector config pinned to the paper profile.
        feed: :class:`MarketFeed` used to fetch current prices for
            unrealized-P&L calculation.
    """

    def __init__(
        self,
        config: AutopilotConfig | None = None,
        okx_config: OKXConfig | None = None,
    ) -> None:
        """Initialize the paper engine.

        Args:
            config: Autopilot config; loaded from env when ``None``.
            okx_config: OKX connector config; defaults to a paper-profile
                config (``OKXConfig(profile="paper")``) when ``None``.
        """
        self.config: AutopilotConfig = config or load_autopilot_config()
        self.okx_config: OKXConfig = okx_config or OKXConfig(profile="paper")
        self.feed: MarketFeed = MarketFeed(okx_config=self.okx_config)

        # Local position book: symbol -> list of open fill records.
        # Each record: {symbol, side, quantity, entry_price, entry_time}
        self._positions: dict[str, list[dict[str, Any]]] = defaultdict(list)

        # Daily P&L history: [(date_iso, pnl_usd), ...]
        self._daily_pnl: list[tuple[str, float]] = []

        # Trade log: list of {ts, symbol, side, quantity, price, notional}
        self._trade_log: list[dict[str, Any]] = []

        # Realized P&L accumulated today (reset on daily roll)
        self._realized_today: float = 0.0
        self._current_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_order(self, symbol: str, side: str, notional: float) -> dict[str, Any]:
        """Place a market order on the OKX demo account.

        Enforces the per-order notional cap from
        :attr:`config.max_order_notional_usd` before delegating to the SDK.
        On a successful fill the local position book and trade log are
        updated.

        Args:
            symbol: Instrument id, e.g. ``"BTC-USDT"``.
            side: ``"buy"`` or ``"sell"``.
            notional: Quote-currency amount (USD) to trade.

        Returns:
            The SDK order-result dict (``{"status": "ok", ...}`` or
            ``{"status": "error", ...}``).

        Raises:
            ValueError: When *notional* exceeds the configured cap.
        """
        clean_symbol = symbol.strip().upper()
        clean_side = side.strip().lower()

        if notional > self.config.max_order_notional_usd:
            msg = (
                f"notional {notional:.2f} exceeds max "
                f"{self.config.max_order_notional_usd:.2f}"
            )
            logger.warning("place_order rejected: %s", msg)
            raise ValueError(msg)

        result = okx_sdk.place_order(
            self.okx_config,
            symbol=clean_symbol,
            side=clean_side,
            notional=notional,
            order_type="market",
        )

        if result.get("status") == "ok":
            self._record_fill(clean_symbol, clean_side, notional)
        else:
            logger.warning(
                "place_order failed for %s %s: %s",
                clean_side,
                clean_symbol,
                result.get("error"),
            )
        return result

    # ------------------------------------------------------------------
    # Position book
    # ------------------------------------------------------------------

    def get_positions(self) -> list[PaperPosition]:
        """Return current open positions with unrealized P&L.

        Fetches the latest market price for each symbol via
        :class:`MarketFeed` and computes unrealized P&L against the
        average entry price.

        Returns:
            List of frozen :class:`PaperPosition` instances.
        """
        self._roll_daily_if_needed()
        result: list[PaperPosition] = []
        for symbol, fills in self._positions.items():
            if not fills:
                continue
            total_qty = sum(f["quantity"] for f in fills)
            if total_qty <= 0:
                continue
            avg_entry = sum(f["entry_price"] * f["quantity"] for f in fills) / total_qty
            current_price = self._current_price(symbol)
            unrealized = (
                (current_price - avg_entry) * total_qty
                if current_price is not None
                else 0.0
            )
            entry_time = min(f["entry_time"] for f in fills)
            result.append(
                PaperPosition(
                    symbol=symbol,
                    side="long",
                    quantity=total_qty,
                    entry_price=avg_entry,
                    entry_time=entry_time,
                    unrealized_pnl=unrealized,
                )
            )
        return result

    # ------------------------------------------------------------------
    # P&L computation
    # ------------------------------------------------------------------

    def compute_daily_pnl(self) -> float:
        """Calculate today's total P&L (realized + unrealized).

        The realized portion accumulates from closed trades since the
        last daily roll.  The unrealized portion is the sum of mark-to-
        market P&L on all open positions.

        The result is appended to the daily P&L history (one entry per
        UTC calendar day; repeated calls on the same day replace the
        previous entry).

        Returns:
            Today's P&L in USD.
        """
        self._roll_daily_if_needed()
        unrealized = sum(p.unrealized_pnl for p in self.get_positions())
        daily_pnl = self._realized_today + unrealized

        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Replace same-day entry or append new day.
        if self._daily_pnl and self._daily_pnl[-1][0] == today_iso:
            self._daily_pnl[-1] = (today_iso, daily_pnl)
        else:
            self._daily_pnl.append((today_iso, daily_pnl))
        return daily_pnl

    def get_daily_pnl_history(self) -> list[tuple[str, float]]:
        """Return the recorded daily P&L series.

        Returns:
            List of ``(date_iso, pnl_usd)`` tuples, oldest-first.
        """
        return list(self._daily_pnl)

    # ------------------------------------------------------------------
    # Risk metrics
    # ------------------------------------------------------------------

    def compute_rolling_sharpe(self, window_days: int = 30) -> float:
        """Compute annualised Sharpe ratio over the last *window_days*.

        Uses :attr:`config.bars_per_year` (default 365) as the
        annualisation factor because crypto trades 24/7.

        Args:
            window_days: Number of most-recent daily P&L observations
                to include.

        Returns:
            Annualised Sharpe ratio, or ``0.0`` when fewer than 2 data
            points are available.
        """
        pnl = [v for _, v in self._daily_pnl[-window_days:]]
        if len(pnl) < 2:
            return 0.0
        arr = np.array(pnl, dtype=np.float64)
        mean = float(np.mean(arr))
        std = float(np.std(arr, ddof=1))
        if std == 0.0:
            return 0.0
        return mean / std * np.sqrt(self.config.bars_per_year)

    def compute_max_drawdown(self) -> float:
        """Compute maximum drawdown from the cumulative P&L curve.

        Returns:
            Maximum drawdown as a non-negative fraction (0.15 = 15%).
            Returns ``0.0`` when there are fewer than 2 daily entries.
        """
        if len(self._daily_pnl) < 2:
            return 0.0
        cum = np.cumsum([v for _, v in self._daily_pnl], dtype=np.float64)
        running_max = np.maximum.accumulate(cum)
        drawdowns = running_max - cum
        peak = float(np.max(running_max))
        max_dd = float(np.max(drawdowns))
        if peak <= 0:
            return 0.0
        return max_dd / peak

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def close_all_positions(self) -> None:
        """Market-sell every open position and record realized P&L.

        Failures on individual symbols are logged but do not prevent
        other positions from being closed.
        """
        for symbol in list(self._positions.keys()):
            fills = self._positions.get(symbol, [])
            if not fills:
                continue
            total_qty = sum(f["quantity"] for f in fills)
            if total_qty <= 0:
                continue
            avg_entry = sum(f["entry_price"] * f["quantity"] for f in fills) / total_qty
            current_price = self._current_price(symbol)
            if current_price is None:
                logger.warning(
                    "close_all_positions: cannot price %s, skipping", symbol
                )
                continue
            # Place a market sell for the full quantity.
            notional = total_qty * current_price
            result = okx_sdk.place_order(
                self.okx_config,
                symbol=symbol,
                side="sell",
                quantity=total_qty,
                order_type="market",
            )
            if result.get("status") == "ok":
                realized = (current_price - avg_entry) * total_qty
                self._realized_today += realized
                self._trade_log.append(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "symbol": symbol,
                        "side": "sell",
                        "quantity": total_qty,
                        "price": current_price,
                        "notional": notional,
                        "realized_pnl": realized,
                    }
                )
                self._positions[symbol] = []
                logger.info(
                    "closed %s: qty=%.6f realized=%.2f",
                    symbol,
                    total_qty,
                    realized,
                )
            else:
                logger.warning(
                    "close_all_positions: sell %s failed: %s",
                    symbol,
                    result.get("error"),
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_fill(self, symbol: str, side: str, notional: float) -> None:
        """Update the local position book after a successful fill.

        Fetches the current market price to derive quantity and entry
        price.  Buy orders open (or add to) a long position; sell orders
        reduce or close it.
        """
        price = self._current_price(symbol)
        if price is None or price <= 0:
            logger.warning(
                "_record_fill: no price for %s, using notional as proxy", symbol
            )
            price = notional  # fallback: treat as 1 unit
        quantity = notional / price

        now = datetime.now(timezone.utc)
        self._trade_log.append(
            {
                "ts": now.isoformat(),
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "notional": notional,
            }
        )

        if side == "buy":
            self._positions[symbol].append(
                {
                    "symbol": symbol,
                    "side": "long",
                    "quantity": quantity,
                    "entry_price": price,
                    "entry_time": now,
                }
            )
        elif side == "sell":
            # Reduce or close existing longs.
            remaining = quantity
            while remaining > 0 and self._positions[symbol]:
                fill = self._positions[symbol][0]
                if fill["quantity"] <= remaining:
                    # Fully close this fill.
                    realized = (price - fill["entry_price"]) * fill["quantity"]
                    self._realized_today += realized
                    remaining -= fill["quantity"]
                    self._positions[symbol].pop(0)
                else:
                    # Partially close.
                    realized = (price - fill["entry_price"]) * remaining
                    self._realized_today += realized
                    fill["quantity"] -= remaining
                    remaining = 0.0

    def _current_price(self, symbol: str) -> float | None:
        """Fetch the latest mid price for *symbol* via MarketFeed.

        Returns ``None`` when the price cannot be determined.
        """
        try:
            bars = self.feed.fetch_bars(symbol, period="1d", limit=1)
            if bars is not None and not bars.empty:
                return float(bars["close"].iloc[-1])
        except Exception as exc:  # noqa: BLE001 — price fetch is best-effort
            logger.debug("_current_price(%s) failed: %s", symbol, exc)
        return None

    def _roll_daily_if_needed(self) -> None:
        """Reset daily realized P&L when the UTC date rolls over."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            self._current_date = today
            self._realized_today = 0.0
