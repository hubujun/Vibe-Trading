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
from pathlib import Path
from typing import Any

import numpy as np

from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.crypto_autopilot.daily_counter import DailyOrderCounter
from src.crypto_autopilot.market_feed import MarketFeed
from src.crypto_autopilot.trade_ledger import (
    append_slippage_record,
    read_trade_records,
    write_trade_record,
)
from src.crypto_autopilot.types import PaperPosition
from src.trading.connectors.okx import sdk as okx_sdk
from src.trading.connectors.okx.sdk import OKXConfig, OKXConfigError, load_config

logger = logging.getLogger(__name__)

__all__ = ["PaperEngine"]


def _default_runtime_root() -> Path:
    """Return the default autopilot runtime root for the trade ledger."""
    return Path(__file__).resolve().parents[2] / "runs" / "autopilot"


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
        runtime_root: Path | None = None,
    ) -> None:
        """Initialize the paper engine.

        Args:
            config: Autopilot config; loaded from env when ``None``.
            okx_config: OKX connector config; defaults to a paper-profile
                config (``OKXConfig(profile="paper")``) when ``None``.
            runtime_root: Directory for the trade ledger
                (``<runtime_root>/trades.jsonl``).  Defaults to the autopilot
                runtime root.  When a ledger exists, fills are replayed at
                startup so the position book and trade log survive restarts.
        """
        self.config: AutopilotConfig = config or load_autopilot_config()
        if okx_config is None:
            # Credentials come from the shared runtime config (~/.vibe-trading/
            # okx.json) but the engine stays pinned to the demo environment — a
            # live key sent to the demo host fails closed at the broker instead
            # of touching real funds.
            try:
                self.okx_config: OKXConfig = load_config().with_overrides(
                    profile="paper"
                )
            except (OKXConfigError, Exception) as exc:  # noqa: BLE001
                logger.warning(
                    "could not load OKX runtime config (%s); using bare paper config",
                    exc,
                )
                self.okx_config = OKXConfig(profile="paper")
        else:
            self.okx_config = okx_config
        self.feed: MarketFeed = MarketFeed(okx_config=self.okx_config)
        self._ledger_root: Path = runtime_root or _default_runtime_root()

        # Persisted per-UTC-day order counter (survives restarts). Paper
        # fills count toward the same daily cap as live orders so the
        # monitoring dashboard reflects paper activity too.
        self._daily_counter: DailyOrderCounter = DailyOrderCounter(self._ledger_root)

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

        # Replay persisted fills so the position book survives restarts.
        self._restore_from_ledger()

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

        # Enforce the daily order cap up front — applies to both the local
        # simulated fills and the broker path, matching the mandate gate the
        # live executor uses. Sells are exempt: closing a position must never
        # be blocked by the quota.
        if clean_side == "buy" and self._daily_counter.count_today() >= self.config.max_trades_per_day:
            logger.warning(
                "place_order rejected: daily order limit reached "
                "(%d/%d)",
                self._daily_counter.count_today(),
                self.config.max_trades_per_day,
            )
            return {
                "status": "error",
                "error": "daily order limit reached",
                "symbol": clean_symbol,
                "side": clean_side,
            }

        # Enforce the aggregate exposure cap on buys: never open beyond
        # max_total_exposure_usd across all open positions.
        if clean_side == "buy":
            exposure = self.open_exposure_usd()
            if exposure + notional > self.config.max_total_exposure_usd:
                logger.warning(
                    "place_order rejected: exposure limit reached "
                    "(%.2f + %.2f > %.2f)",
                    exposure, notional, self.config.max_total_exposure_usd,
                )
                return {
                    "status": "error",
                    "error": "exposure limit reached",
                    "symbol": clean_symbol,
                    "side": clean_side,
                }

        if self.config.paper_simulated:
            # Local simulated fills: no broker round-trip, no API key. The
            # position book and trade ledger still update exactly like a
            # real demo fill so the full pipeline can be exercised end-to-end.
            return self._place_simulated(clean_symbol, clean_side, notional)

        # Snapshot the signal price before the round-trip so slippage can be
        # measured against the actual fill price (best-effort).
        signal_price = self._current_price(clean_symbol)
        result = okx_sdk.place_order(
            self.okx_config,
            symbol=clean_symbol,
            side=clean_side,
            notional=notional,
            order_type="market",
        )

        if result.get("status") == "ok":
            self._record_fill(
                clean_symbol, clean_side, notional, signal_price=signal_price,
            )
        else:
            logger.warning(
                "place_order failed for %s %s: %s",
                clean_side,
                clean_symbol,
                result.get("error"),
            )
        return result

    def _place_simulated(self, symbol: str, side: str, notional: float) -> dict[str, Any]:
        """Fill an order locally against the live market price.

        Used when ``config.paper_simulated`` is set (``AUTOPILOT_PAPER_SIMULATED=1``):
        no OKX credentials are needed and nothing reaches the broker. The fill
        updates the position book and appends to the trade ledger exactly like
        a real demo fill, so monitoring, risk metrics, and promotion all work.

        Args:
            symbol: Clean instrument id (``BTC-USDT``).
            side: ``"buy"`` or ``"sell"``.
            notional: Quote-currency amount (USD) to trade.

        Returns:
            An OKX-shaped result dict with ``status: "ok"`` plus a
            ``simulated: True`` marker.
        """
        price = self._current_price(symbol)
        if price is None or price <= 0:
            logger.warning(
                "_place_simulated: no price for %s; cannot simulate fill", symbol
            )
            return {
                "status": "error",
                "error": f"no market price for {symbol}",
                "symbol": symbol,
                "side": side,
                "simulated": True,
            }
        self._record_fill(symbol, side, notional, signal_price=price)
        logger.info(
            "simulated fill %s %s notional=%.2f @ %.4f",
            side, symbol, notional, price,
        )
        return {
            "status": "ok",
            "simulated": True,
            "symbol": symbol,
            "side": side,
            "notional": notional,
            "fill_price": price,
        }

    # ------------------------------------------------------------------
    # Position book
    # ------------------------------------------------------------------

    def open_exposure_usd(self) -> float:
        """Sum of mark-to-market notional across all open positions.

        Returns:
            Total exposure in USD at current market prices (0.0 when no
            positions are open or prices are unavailable).
        """
        total = 0.0
        for symbol, fills in self._positions.items():
            qty = sum(f["quantity"] for f in fills)
            if qty <= 0:
                continue
            price = self._current_price(symbol)
            if price is None or price <= 0:
                continue
            total += qty * price
        return total

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
            # Cost basis includes buy-side fees so unrealized P&L is net of
            # paid costs, matching the net-of-fee realized accounting.
            total_cost = sum(
                f["entry_price"] * f["quantity"] + f.get("fee", 0.0)
                for f in fills
            )
            avg_cost = total_cost / total_qty
            current_price = self._current_price(symbol)
            unrealized = (
                (current_price - avg_cost) * total_qty
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

    def close_position(self, symbol: str) -> dict[str, Any]:
        """Market-sell a single symbol's open position (local fill when simulated).

        Sells are never blocked by the daily order quota or the exposure
        cap — reducing risk is always allowed.

        Args:
            symbol: Instrument id, e.g. ``"BTC-USDT"``.

        Returns:
            An OKX-shaped result dict (``status: "ok"`` with a
            ``simulated: True`` marker in simulated mode, or an error
            dict when there is nothing to close).
        """
        symbol = symbol.strip().upper()
        fills = self._positions.get(symbol, [])
        if not fills:
            return {
                "status": "error",
                "error": f"no open position for {symbol}",
                "symbol": symbol,
                "side": "sell",
            }
        total_qty = sum(f["quantity"] for f in fills)
        if total_qty <= 0:
            return {
                "status": "error",
                "error": f"no open quantity for {symbol}",
                "symbol": symbol,
                "side": "sell",
            }
        avg_entry = sum(f["entry_price"] * f["quantity"] for f in fills) / total_qty
        current_price = self._current_price(symbol)
        if current_price is None or current_price <= 0:
            return {
                "status": "error",
                "error": f"no market price for {symbol}",
                "symbol": symbol,
                "side": "sell",
            }
        notional = total_qty * current_price

        if self.config.paper_simulated:
            # Local close: record the sell fill and let the position book
            # settle realized P&L exactly like a broker fill (net of fees).
            realized = self._record_fill(
                symbol, "sell", notional, signal_price=current_price,
            )
            logger.info(
                "simulated close %s: qty=%.6f realized=%.2f",
                symbol, total_qty, realized or 0.0,
            )
            return {
                "status": "ok",
                "simulated": True,
                "symbol": symbol,
                "side": "sell",
                "notional": notional,
                "fill_price": current_price,
                "realized_pnl": realized,
            }

        result = okx_sdk.place_order(
            self.okx_config,
            symbol=symbol,
            side="sell",
            quantity=total_qty,
            order_type="market",
        )
        if result.get("status") == "ok":
            fee = notional * self.config.fee_rate_taker
            realized = (current_price - avg_entry) * total_qty - fee
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
                    "fee": fee,
                }
            )
            self._positions[symbol] = []
            self._write_ledger(
                symbol=symbol,
                side="sell",
                quantity=total_qty,
                price=current_price,
                notional=notional,
                realized_pnl=realized,
                fee=fee,
            )
            logger.info(
                "closed %s: qty=%.6f realized=%.2f",
                symbol, total_qty, realized,
            )
        else:
            logger.warning(
                "close_position: sell %s failed: %s",
                symbol, result.get("error"),
            )
        return result

    def close_all_positions(self) -> None:
        """Market-sell every open position and record realized P&L.

        Failures on individual symbols are logged but do not prevent
        other positions from being closed.
        """
        for symbol in list(self._positions.keys()):
            try:
                result = self.close_position(symbol)
                if result.get("status") != "ok":
                    logger.warning(
                        "close_all_positions: %s failed: %s",
                        symbol, result.get("error"),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "close_all_positions: %s error: %s", symbol, exc,
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_fill(
        self, symbol: str, side: str, notional: float, signal_price: float | None = None,
    ) -> float | None:
        """Update the local position book after a successful fill.

        Fetches the current market price to derive quantity and entry
        price.  Buy orders open (or add to) a long position; sell orders
        reduce or close it.  The fill is also appended to the persisted
        trade ledger so the position book survives restarts.

        Args:
            symbol: Instrument id.
            side: ``"buy"`` or ``"sell"``.
            notional: Quote-currency amount of the fill.
            signal_price: Price the strategy observed when it decided to
                trade; used to measure slippage against the fill price
                (best-effort, ``None`` skips the measurement).

        Returns:
            Realized P&L (net of fees) when the fill closed a position,
            else ``None``.
        """
        fee = notional * self.config.fee_rate_taker
        price = self._current_price(symbol)
        if price is None or price <= 0:
            logger.warning(
                "_record_fill: no price for %s, using notional as proxy", symbol
            )
            price = notional  # fallback: treat as 1 unit
        quantity = notional / price

        now = datetime.now(timezone.utc)
        realized = self._apply_fill(
            symbol, side, price, quantity, now, tally_realized=True, fee=fee,
        )
        self._write_ledger(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            notional=notional,
            realized_pnl=realized,
            fee=fee,
        )
        # Measure signal-vs-fill spread when a signal price is available and
        # actually differs from the fill price (identical prices mean zero
        # slippage; skip the noise).
        if (
            signal_price is not None
            and signal_price > 0
            and abs(signal_price - price) > 1e-12
        ):
            append_slippage_record(
                self._ledger_root,
                symbol=symbol,
                signal_price=signal_price,
                fill_price=price,
            )
        # Count the accepted fill toward the persisted daily cap.
        try:
            self._daily_counter.increment()
        except OSError as exc:
            logger.warning(
                "could not persist daily order counter (%s) — "
                "the fill still stands",
                exc,
            )
        return realized

    def _apply_fill(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        now: datetime,
        *,
        tally_realized: bool,
        fee: float = 0.0,
    ) -> float | None:
        """Apply one fill to the position book and trade log (no I/O).

        Buy orders open (or add to) a long position, carrying their fee in
        the position record; sell orders reduce or close it, computing
        realized P&L net of the buy-side fee (pro-rated on partial closes)
        and the sell-side fee.  With ``tally_realized=False`` the realized
        P&L is returned but not accumulated (used when replaying the ledger
        at startup, where only today's closures belong in
        ``_realized_today``).

        Args:
            symbol: Instrument id.
            side: ``"buy"`` or ``"sell"``.
            price: Fill price.
            quantity: Filled quantity.
            now: Fill timestamp.
            tally_realized: Whether sell-side realized P&L is accumulated
                into :attr:`_realized_today`.
            fee: Trading fee of this fill (fraction of notional already
                resolved by the caller).

        Returns:
            Realized P&L for sell fills that closed positions, else ``None``.
        """
        self._trade_log.append(
            {
                "ts": now.isoformat(),
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "notional": price * quantity,
                "fee": fee,
            }
        )

        realized: float | None = None
        if side == "buy":
            self._positions[symbol].append(
                {
                    "symbol": symbol,
                    "side": "long",
                    "quantity": quantity,
                    "entry_price": price,
                    "entry_time": now,
                    "fee": fee,
                }
            )
        elif side == "sell":
            # Reduce or close existing longs. Realized P&L is net of fees:
            # the buy-side fee stored on the fill (pro-rated on partial
            # closes) and this sell's own fee.
            remaining = quantity
            while remaining > 0 and self._positions[symbol]:
                fill = self._positions[symbol][0]
                closed_qty = min(fill["quantity"], remaining)
                buy_fee_share = fill.get("fee", 0.0) * (closed_qty / fill["quantity"])
                sell_fee_share = fee * (closed_qty / quantity)
                closed = (
                    (price - fill["entry_price"]) * closed_qty
                    - buy_fee_share - sell_fee_share
                )
                if tally_realized:
                    self._realized_today += closed
                realized = (realized or 0.0) + closed
                if fill["quantity"] <= remaining:
                    remaining -= fill["quantity"]
                    self._positions[symbol].pop(0)
                else:
                    fill["quantity"] -= remaining
                    # Consume the fee share this close paid for so a later
                    # close of the remainder cannot double-charge it.
                    fill["fee"] = fill.get("fee", 0.0) - buy_fee_share
                    remaining = 0.0
        return realized

    def _write_ledger(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        notional: float,
        realized_pnl: float | None,
        fee: float = 0.0,
    ) -> None:
        """Append one paper fill to the trade ledger (best-effort)."""
        write_trade_record(
            self._ledger_root,
            engine="paper",
            symbol=symbol,
            side=side,
            notional=notional,
            quantity=quantity,
            price=price,
            realized_pnl=realized_pnl,
            fee=fee,
        )

    def _restore_from_ledger(self) -> None:
        """Replay persisted paper fills to rebuild the position book.

        Reads the trade ledger (oldest first), applies every fill with
        ``tally_realized=False``, then accumulates only today's realized
        P&L so the daily counter stays correct across restarts.  Corrupt or
        price-less records are skipped.  Failures never raise — a broken
        ledger degrades to a cold start.
        """
        try:
            records = read_trade_records(
                self._ledger_root, engine="paper", limit=10_000,
            )
        except Exception as exc:  # noqa: BLE001 — cold start on any failure
            logger.warning("paper ledger restore failed: %s", exc)
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for record in reversed(records):  # oldest first
            quantity = record.get("quantity")
            price = record.get("price")
            if quantity is None or price is None:
                continue
            try:
                ts = datetime.fromisoformat(str(record.get("ts", "")))
            except ValueError:
                ts = datetime.now(timezone.utc)
            realized = self._apply_fill(
                str(record.get("symbol", "")),
                str(record.get("side", "")),
                float(price),
                float(quantity),
                ts,
                tally_realized=False,
                fee=float(record.get("fee", 0.0) or 0.0),
            )
            if realized is not None and ts.strftime("%Y-%m-%d") == today:
                self._realized_today += realized
        if self._trade_log:
            logger.info(
                "paper ledger restore: replayed %d fill(s)", len(self._trade_log),
            )

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
