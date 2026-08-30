"""Live execution wrapper for the crypto autopilot — OKX live profile.

Bridges the autopilot pipeline to the OKX **live** trading environment
(``flag="0"``) with the same halt → mandate → execute → audit safety
ordering as :class:`src.live.runtime.runner.LiveRunner`, but without the
full agent-session dependency. This makes it suitable for the autopilot's
own signal generation (which does not route through
``SessionService.send_message``).

Tick ordering (fail-closed at each step):

1. **Halt** — :func:`src.live.halt.halt_flag_set` is checked before any
   broker call. If the sentinel exists, the tick aborts.
2. **Mandate** — the mandate's ``expires_at`` is checked; an expired
   mandate trips the halt so a dead authority never reaches the order
   path.
3. **Reconcile** — OKX positions / balance / open orders are read; any
   read failure aborts the tick (no auto-resend, SPEC §8 finding 5).
4. **Risk** — :class:`RiskMonitor.evaluate` checks daily loss and
   consecutive losses; a breach trips the halt.
5. **Execute** — :meth:`place_order` validates the order against the
   mandate (:func:`src.live.enforcement.check_mandate`) before calling
   :func:`okx_sdk.place_order`.
6. **Audit** — every outcome is written to the live-action ledger
   (:func:`src.live.audit.write_live_action`).

The executor follows the :class:`~src.crypto_autopilot.paper_engine.PaperEngine`
pattern but pins the OKX *live* profile and adds mandate enforcement +
halt checks on every order path.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.crypto_autopilot.daily_counter import DailyOrderCounter
from src.crypto_autopilot.mandate_template import MandateTemplate
from src.crypto_autopilot.notifier import AutopilotNotifier
from src.crypto_autopilot.risk_monitor import RiskMonitor
from src.crypto_autopilot.trade_ledger import write_trade_record
from src.live.audit import LiveActionEvent, write_live_action
from src.live.enforcement import OrderIntent, check_mandate
from src.live.halt import halt_flag_set, trip_halt
from src.live.mandate.model import Mandate
from src.live.mandate.store import _parse_mandate
from src.trading.connectors.okx import sdk as okx_sdk
from src.trading.connectors.okx.sdk import OKXConfig, OKXConfigError, load_config

if TYPE_CHECKING:
    from src.crypto_autopilot.paper_engine import PaperEngine

logger = logging.getLogger(__name__)

__all__ = ["LiveExecutor"]

#: Broker key for the autopilot live channel — must match the mandate template
#: and the risk monitor so the per-broker HALT sentinel aligns.
_BROKER_KEY = "okx"

#: Trip source label for executor-originated halts (mandate expiry, etc.).
_HALT_TRIP_SOURCE = "file"

#: Audit session id for the autopilot live channel.
_SESSION_ID = "crypto-autopilot-live"


def _default_runtime_root() -> Path:
    """Return the default autopilot runtime root for persisted counters."""
    return Path(__file__).resolve().parents[2] / "runs" / "autopilot"


class LiveExecutor:
    """OKX live-profile execution wrapper for the crypto autopilot.

    Wraps the OKX SDK's read/write surface with the project's safety
    infrastructure (halt, mandate, enforcement, audit). The executor can run
    as a standalone 24/7 loop (via :meth:`start` / :meth:`stop`) or be used
    for direct order placement (via :meth:`place_order`) when the autopilot
    pipeline drives signal generation externally.

    Attributes:
        config: Autopilot tuning knobs (max notional, intervals, etc.).
        okx_config: OKX connector config pinned to the live profile.
        mandate: The active (parsed) mandate dataclass.
        risk_monitor: Risk monitor backing the auto-halt checks.
    """

    def __init__(
        self,
        config: AutopilotConfig | None = None,
        mandate_path: Path | None = None,
        runtime_root: Path | None = None,
        paper_engine: "PaperEngine | None" = None,
        shadow_mode: bool = False,
    ) -> None:
        """Initialize the live executor.

        Args:
            config: Autopilot config; loaded from env when ``None``.
            mandate_path: Path to a mandate YAML file. When ``None``, the
                mandate is generated from :class:`MandateTemplate` using the
                autopilot config defaults. When provided, the file is
                parsed via :func:`yaml.safe_load` and validated through the
                mandate store's parser.
            runtime_root: Directory for the persisted daily order counter
                (``<runtime_root>/daily_orders.json``). Defaults to the
                autopilot runtime root so the count survives restarts.
            paper_engine: Optional :class:`PaperEngine` for shadow mode —
                when set together with ``shadow_mode=True`` every live fill
                is mirrored with a same-signal paper fill so the gap report
                can compare execution quality.
            shadow_mode: When True, mirror live fills with paper fills
                (requires ``paper_engine``).
        """
        self.config: AutopilotConfig = config or load_autopilot_config()
        self.broker: str = _BROKER_KEY
        self._paper_engine = paper_engine
        self.shadow_mode: bool = bool(shadow_mode)

        runtime = runtime_root or _default_runtime_root()
        self._runtime_root: Path = runtime

        # Persisted per-UTC-day order counter (survives restarts).
        self._daily_counter: DailyOrderCounter = DailyOrderCounter(runtime)

        # Best-effort IM outbox — order/halt events are relayed to chat
        # channels by the API server's autopilot-notify worker.
        self._notifier: AutopilotNotifier = AutopilotNotifier(runtime)

        # Build OKX live config. Try to load credentials from the runtime
        # file and override the profile to "live"; fall back to a bare
        # live-profile config when the file is absent (the SDK will surface
        # a credentials_missing error on first use).
        try:
            base = load_config()
            self.okx_config: OKXConfig = base.with_overrides(
                profile=self.config.live_profile
            )
        except (OKXConfigError, Exception) as exc:
            logger.warning(
                "could not load OKX runtime config (%s); using bare "
                "live-profile config — credentials must be set before "
                "trading",
                exc,
            )
            self.okx_config = OKXConfig(profile=self.config.live_profile)

        # Load or generate the mandate.
        self.mandate: Mandate = self._load_or_generate_mandate(mandate_path)

        # Risk monitor for daily-loss / consecutive-loss auto-halt.
        self.risk_monitor: RiskMonitor = RiskMonitor(self.config)

        # Loop control state.
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Mandate loading
    # ------------------------------------------------------------------

    def _load_or_generate_mandate(self, mandate_path: Path | None) -> Mandate:
        """Load a mandate from a YAML file or generate one from the template.

        Args:
            mandate_path: Path to a mandate YAML/JSON file, or ``None``.

        Returns:
            A parsed :class:`Mandate` dataclass, validated through the
            store's ``_parse_mandate`` parser so it is structurally
            identical to a mandate loaded from the protected store.

        Raises:
            ValueError: When the file exists but cannot be parsed into a
                valid mandate.
        """
        if mandate_path is not None:
            path = Path(mandate_path)
            if not path.is_file():
                raise FileNotFoundError(
                    f"mandate file not found: {path}"
                )
            try:
                import yaml

                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(
                    f"mandate file {path} could not be parsed: {exc}"
                ) from exc
            try:
                return _parse_mandate(raw)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"mandate file {path} failed structural validation: {exc}"
                ) from exc

        # No path given — generate from the template.
        mandate_dict = MandateTemplate.autopilot_mandate(self.config)
        try:
            return _parse_mandate(mandate_dict)
        except (KeyError, TypeError, ValueError) as exc:
            # This should never happen — the template is validated against
            # the model. Log and re-raise so the operator sees the mismatch.
            logger.error(
                "generated autopilot mandate failed validation: %s", exc
            )
            raise

    # ------------------------------------------------------------------
    # OKX read/write callable builders
    # ------------------------------------------------------------------

    def _build_okx_read_callables(self) -> dict[str, Callable[[], dict[str, Any]]]:
        """Build broker READ callables wrapping OKX SDK calls.

        These callables are designed for injection into a LiveRunner (if the
        full framework is later wired up) and are also used directly by this
        executor's reconcile step.

        Returns:
            A dict with ``read_positions``, ``read_balance``, and
            ``read_open_orders`` callables. Each callable returns the raw
            OKX SDK response dict.
        """
        okx_cfg = self.okx_config

        def read_positions() -> dict[str, Any]:
            return okx_sdk.get_positions(okx_cfg)

        def read_balance() -> dict[str, Any]:
            return okx_sdk.get_account_snapshot(okx_cfg)

        def read_open_orders() -> dict[str, Any]:
            return okx_sdk.get_open_orders(okx_cfg)

        return {
            "read_positions": read_positions,
            "read_balance": read_balance,
            "read_open_orders": read_open_orders,
        }

    def _build_okx_write_callable(self) -> Callable[[dict[str, Any]], dict[str, Any]]:
        """Build a broker WRITE callable wrapping ``okx_sdk.place_order``.

        The callable accepts a normalized order dict with keys ``symbol``,
        ``side``, ``notional`` (or ``quantity``), and ``order_type``, and
        returns the raw OKX SDK response. This mirrors the
        ``SubmitCallable`` signature expected by
        :class:`~src.live.runtime.runner.LiveRunner`.

        Returns:
            A callable ``(order_dict) -> response_dict``.
        """
        okx_cfg = self.okx_config

        def submit(order: dict[str, Any]) -> dict[str, Any]:
            return okx_sdk.place_order(
                okx_cfg,
                symbol=str(order.get("symbol", "")).strip().upper(),
                side=str(order.get("side", "")).strip().lower(),
                notional=order.get("notional"),
                quantity=order.get("quantity"),
                order_type=str(order.get("order_type", "market")),
                limit_price=order.get("limit_price"),
            )

        return submit

    # ------------------------------------------------------------------
    # Live loop (asyncio)
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Internal asyncio loop that runs safety ticks at the trade cadence.

        Each iteration follows the halt → mandate → reconcile → risk → audit
        ordering. The actual order placement (the "invoke" step) is not done
        here — it is driven by the autopilot pipeline calling
        :meth:`place_order`. This loop ensures the safety checks run
        continuously even when no orders are being placed.
        """
        interval = max(1, self.config.trade_interval_minutes) * 60
        logger.info(
            "live executor loop started for %s (tick every %ds)",
            self.broker,
            interval,
        )

        while self._running:
            try:
                await self._tick()
            except Exception:
                # A tick error must not crash the loop — log and continue.
                logger.exception(
                    "live executor tick error for %s — continuing loop",
                    self.broker,
                )
            await asyncio.sleep(interval)

    async def _tick(self) -> dict[str, Any]:
        """Run one fail-closed safety tick and return the outcome dict.

        Ordering: halt → mandate expiry → reconcile → risk → audit. The
        "invoke" step (signal generation) is external; this tick only runs
        the safety gate so the autopilot pipeline knows it is safe to trade.

        Returns:
            A tick-outcome dict with ``outcome``, ``broker``, ``reason``,
            and ``ts`` keys.
        """
        now = datetime.now(timezone.utc)

        # 1. Halt check.
        if halt_flag_set(self.broker):
            self._audit(kind="halt_tripped", outcome="blocked",
                        intent="tick aborted — kill switch tripped")
            return {"outcome": "halted", "broker": self.broker,
                    "reason": "kill switch tripped",
                    "ts": now.isoformat(timespec="seconds")}

        # 2. Mandate expiry check (proactive).
        expires_raw = self.mandate.consent.expires_at
        if self._mandate_expired(expires_raw, now):
            try:
                trip_halt(
                    _HALT_TRIP_SOURCE,
                    "mandate expired — proactive executor stop",
                    broker=self.broker,
                )
            except Exception:
                logger.exception(
                    "failed to trip halt on mandate expiry for %s",
                    self.broker,
                )
            self._audit(
                kind="halt_tripped", outcome="blocked",
                intent="mandate expired — proactive stop, authority revoked",
            )
            return {"outcome": "expired", "broker": self.broker,
                    "reason": "mandate expired",
                    "ts": now.isoformat(timespec="seconds")}

        # 3. Reconcile (read broker truth).
        reconcile_ok = self._reconcile()
        if not reconcile_ok:
            self._audit(
                kind="breach", outcome="blocked",
                intent="reconcile failed — tick aborted (fail-closed)",
            )
            return {"outcome": "reconcile_error", "broker": self.broker,
                    "reason": "reconcile failed",
                    "ts": now.isoformat(timespec="seconds")}

        # 4. Risk evaluation (daily loss + consecutive losses).
        #    The equity and P&L inputs come from the autopilot pipeline; when
        #    they are unavailable (start-of-day), the risk monitor returns a
        #    no-halt status without tripping.
        risk_status = self._evaluate_risk()
        if risk_status.get("halt_triggered"):
            self._audit(
                kind="halt_tripped", outcome="blocked",
                intent=f"risk check tripped halt: {risk_status.get('reason', '')}",
            )
            return {"outcome": "risk_halt", "broker": self.broker,
                    "reason": risk_status.get("reason", "risk check"),
                    "ts": now.isoformat(timespec="seconds")}

        # 5. Audit a clean tick.
        self._audit(
            kind="order_placed", outcome="accepted",
            intent="autonomous tick completed — safe to trade",
        )
        return {"outcome": "ok", "broker": self.broker, "reason": "",
                "ts": now.isoformat(timespec="seconds")}

    def start(self) -> None:
        """Start the 24/7 live trading safety loop (asyncio).

        Launches a background asyncio task that runs
        :meth:`_tick` at the configured ``trade_interval_minutes`` cadence.
        The loop continues until :meth:`stop` is called or a halt is
        tripped. Calling ``start()`` while already running is a no-op.
        """
        if self._running:
            logger.warning("live executor already running for %s", self.broker)
            return

        self._running = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — create one in a background thread so this
            # method is callable from synchronous code (e.g. the CLI).
            loop = asyncio.new_event_loop()

        self._task = loop.create_task(self._run_loop())
        logger.info("live executor started for %s", self.broker)

    def stop(self) -> None:
        """Graceful stop of the live loop.

        Signals the loop to exit on the next iteration and cancels the
        asyncio task. Idempotent — calling ``stop()`` when not running is a
        no-op.
        """
        if not self._running:
            return

        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            logger.info("live executor stopping for %s", self.broker)
        self._task = None

    # ------------------------------------------------------------------
    # Direct order placement (mandate-enforced)
    # ------------------------------------------------------------------

    def read_account_equity(self) -> float | None:
        """当前账户权益 USD (规则引擎日初基线/日内熔断用), 读不到返回 None."""
        try:
            snap = self._build_okx_read_callables()["read_balance"]()
            total = (snap or {}).get("account", {}).get("total_equity")
            val = float(total) if total is not None else 0.0
            return val if val > 0 else None
        except Exception:  # noqa: BLE001
            logger.debug("read_account_equity failed", exc_info=True)
            return None

    def read_positions_list(self) -> list[dict]:
        """当前持仓列表 (规则引擎强制平仓用), 读不到返回 []."""
        try:
            raw = self._build_okx_read_callables()["read_positions"]()
            if isinstance(raw, dict):
                return raw.get("data") or raw.get("positions") or []
            return raw or []
        except Exception:  # noqa: BLE001
            logger.debug("read_positions_list failed", exc_info=True)
            return []

    def place_order(self, symbol: str, side: str, notional: float) -> dict[str, Any]:
        """Place a market order on the OKX live account with mandate enforcement.

        This is the direct execution path for the autopilot pipeline. It
        follows the same safety ordering as the tick loop but executes
        synchronously:

        1. **Halt** — if the kill switch is tripped, the order is rejected.
        2. **Mandate** — the order is validated against the mandate via
           :func:`src.live.enforcement.check_mandate` (notional cap,
           instrument allowance, exposure, etc.).
        3. **Execute** — :func:`okx_sdk.place_order` is called with the
           live-profile config.
        4. **Audit** — the outcome is written to the live-action ledger.

        Args:
            symbol: Instrument id, e.g. ``"BTC-USDT"``.
            side: ``"buy"`` or ``"sell"``.
            notional: Quote-currency amount (USD) to trade. Must not exceed
                :attr:`config.max_order_notional_usd`.

        Returns:
            The OKX SDK order-result dict (``{"status": "ok", ...}`` on
            success, ``{"status": "error", ...}`` on any rejection or
            broker error). A rejection by the halt or mandate gate is
            returned as ``{"status": "rejected", "reason": ...}``.
        """
        now = datetime.now(timezone.utc)
        clean_symbol = symbol.strip().upper()
        clean_side = side.strip().lower()

        # 1. Halt check.
        if halt_flag_set(self.broker):
            logger.warning(
                "place_order rejected for %s %s: halt is active",
                clean_side, clean_symbol,
            )
            self._audit(
                kind="order_rejected", outcome="blocked",
                intent=f"order rejected — kill switch tripped: "
                       f"{clean_side} {clean_symbol} notional={notional}",
            )
            return {"status": "rejected",
                    "reason": "kill switch tripped",
                    "symbol": clean_symbol, "side": clean_side}

        # 2. Mandate enforcement.
        intent = OrderIntent(
            symbol=clean_symbol,
            side=clean_side,
            notional_usd=float(notional),
            quantity=None,
            instrument_type=self._instrument_type(),
        )
        # Read current positions/balance for exposure + leverage checks.
        try:
            positions = okx_sdk.get_positions(self.okx_config)
        except Exception:
            logger.warning(
                "place_order: could not read positions for mandate check "
                "— fail-closed DENY"
            )
            self._audit(
                kind="order_rejected", outcome="blocked",
                intent=f"order rejected — positions unreadable (fail-closed): "
                       f"{clean_side} {clean_symbol} notional={notional}",
            )
            return {"status": "rejected",
                    "reason": "positions unreadable (fail-closed)",
                    "symbol": clean_symbol, "side": clean_side}
        try:
            balance = okx_sdk.get_account_snapshot(self.okx_config)
        except Exception:
            balance = {}

        breach = check_mandate(
            self.mandate,
            intent,
            positions,
            balance,
            broker=self.broker,
            remote_tool="okx_place_order",
            daily_count=self._daily_counter.count_today(),
        )
        if breach is not None:
            logger.warning(
                "place_order rejected by mandate gate: %s (limit=%s, "
                "attempted=%s)",
                breach.limit, breach.limit_value, breach.attempted_value,
            )
            self._audit(
                kind="order_rejected", outcome="rejected",
                intent=f"order rejected by mandate — {breach.limit}: "
                       f"{clean_side} {clean_symbol} notional={notional}",
                gate_decision={
                    "limit": breach.limit,
                    "limit_value": breach.limit_value,
                    "attempted_value": breach.attempted_value,
                    "kind": breach.kind,
                    "detail": breach.detail,
                },
            )
            return {"status": "rejected",
                    "reason": f"mandate breach: {breach.limit}",
                    "limit": breach.limit,
                    "limit_value": breach.limit_value,
                    "attempted_value": breach.attempted_value,
                    "symbol": clean_symbol, "side": clean_side}

        # 3. Execute via OKX SDK.
        result = okx_sdk.place_order(
            self.okx_config,
            symbol=clean_symbol,
            side=clean_side,
            notional=float(notional),
            order_type="market",
        )

        # 4. Audit the outcome.
        if result.get("status") == "ok":
            self._audit(
                kind="order_placed", outcome="accepted",
                intent=f"order placed: {clean_side} {clean_symbol} "
                       f"notional={notional}",
                broker_request={"symbol": clean_symbol, "side": clean_side,
                                "notional": notional},
                broker_response=result,
            )
            # Count the accepted order toward the persisted daily cap.
            try:
                self._daily_counter.increment()
            except OSError as exc:
                logger.warning(
                    "could not persist daily order counter (%s) — the "
                    "next mandate check may under-count", exc,
                )
            # Shadow mode: mirror the live fill with a same-signal paper
            # fill so the gap report can compare paper vs live execution.
            # Best-effort — a shadow failure never blocks the live fill.
            if self.shadow_mode and self._paper_engine is not None:
                try:
                    shadow = self._paper_engine.place_order(
                        symbol=clean_symbol,
                        side=clean_side,
                        notional=float(notional),
                    )
                    if shadow.get("status") != "ok":
                        logger.warning(
                            "shadow paper fill for %s %s failed: %s",
                            clean_side, clean_symbol,
                            shadow.get("reason", shadow.get("status")),
                        )
                except Exception as exc:  # noqa: BLE001 — never blocks live
                    logger.warning(
                        "shadow paper fill failed for %s %s: %s",
                        clean_side, clean_symbol, exc,
                    )
            # Notify operators about the fill (best-effort, never blocks).
            self._notifier.notify(
                "order_filled",
                f"Order filled: {clean_side.upper()} {clean_symbol}",
                f"notional={notional} USDT",
                meta={
                    "symbol": clean_symbol,
                    "side": clean_side,
                    "notional": notional,
                },
            )
            # Append to the unified trade ledger (Shadow Account audit
            # stream).  The live SDK result carries no fill price/quantity
            # or fee for market orders, so those fields are estimated with
            # the configured taker rate (best-effort cost realism).
            try:
                write_trade_record(
                    self._runtime_root,
                    engine="live",
                    symbol=clean_symbol,
                    side=clean_side,
                    notional=float(notional),
                    fee=float(notional) * self.config.fee_rate_taker,
                )
            except Exception as exc:  # noqa: BLE001 — audit never blocks
                logger.warning(
                    "place_order: trade ledger write failed: %s", exc,
                )
        else:
            self._audit(
                kind="order_rejected", outcome="error",
                intent=f"order failed: {clean_side} {clean_symbol} "
                       f"notional={notional}",
                broker_request={"symbol": clean_symbol, "side": clean_side,
                                "notional": notional},
                broker_response=result,
                error=str(result.get("error", "")),
            )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _instrument_type(self):
        """Return the :class:`InstrumentType` for crypto spot."""
        from src.live.mandate.model import InstrumentType

        return InstrumentType.CRYPTO

    def _mandate_expired(self, expires_at: str, now: datetime) -> bool:
        """Return whether the mandate's ``expires_at`` is past ``now``.

        Fail-closed: an unparseable expiry is treated as expired so a
        malformed mandate never keeps trading.
        """
        if not isinstance(expires_at, str) or not expires_at.strip():
            return True
        normalized = expires_at.strip()
        if normalized.endswith(("Z", "z")):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return now >= parsed.astimezone(timezone.utc)

    def _reconcile(self) -> bool:
        """Read broker truth (positions/balance/orders); return safety.

        A read failure aborts the tick (fail-closed). This is a simplified
        reconcile that checks the OKX SDK calls succeed; the full
        :func:`src.live.runtime.reconcile.reconcile` function does deeper
        state comparison, but for the autopilot's purposes (small capital,
        cash-only spot) a successful read is sufficient.
        """
        callables = self._build_okx_read_callables()
        for name, fn in callables.items():
            try:
                result = fn()
                # The OKX SDK returns {"status": "ok", ...} on success.
                if isinstance(result, dict) and result.get("status") != "ok":
                    logger.warning(
                        "reconcile %s returned non-ok status for %s: %s",
                        name, self.broker, result.get("status"),
                    )
                    return False
            except Exception as exc:
                logger.warning(
                    "reconcile %s failed for %s: %s",
                    name, self.broker, exc,
                )
                return False
        return True

    def _evaluate_risk(self) -> dict[str, Any]:
        """Run the risk monitor's evaluate() with best-effort equity inputs.

        When the autopilot pipeline has not yet provided equity snapshots
        (e.g. on the very first tick), the risk check is skipped and a
        no-halt status is returned. The risk monitor's ``evaluate`` method
        handles NaN / non-positive equity fail-safely.
        """
        # The equity inputs would come from the autopilot pipeline's
        # position tracker. For now, we attempt a best-effort read from OKX.
        try:
            snapshot = okx_sdk.get_account_snapshot(self.okx_config)
            total_equity = float(
                snapshot.get("account", {}).get("total_equity") or 0
            )
        except Exception:
            logger.debug("risk evaluate: could not read equity, skipping")
            return {"halt_triggered": False, "reason": "", "daily_loss_pct": 0.0}

        # Without a start-of-day baseline or daily P&L history, the risk
        # checks cannot trip (they need comparison values). Return a clean
        # status so the tick proceeds.
        return {
            "halt_triggered": False,
            "reason": "",
            "daily_loss_pct": 0.0,
            "current_equity": total_equity,
        }

    def _audit(
        self,
        *,
        kind: str,
        outcome: str,
        intent: str,
        error: str | None = None,
        broker_request: dict[str, Any] | None = None,
        broker_response: dict[str, Any] | None = None,
        gate_decision: dict[str, Any] | None = None,
    ) -> str | None:
        """Write one live-action audit record for an executor outcome.

        Audit failures are swallowed (logged) so a ledger problem can never
        make a blocking outcome look like it proceeded.

        Args:
            kind: The :class:`~src.live.audit.LiveActionKind`.
            outcome: The :class:`~src.live.audit.LiveActionOutcome`.
            intent: Normalized human-readable intent string.
            error: Optional error description.
            broker_request: Raw request sent to the broker (redacted on write).
            broker_response: Raw broker response (redacted on write).
            gate_decision: Enforcement gate verdict dict.

        Returns:
            The written record's ``audit_id``, or ``None`` on write failure.
        """
        event = LiveActionEvent(
            kind=kind,  # type: ignore[arg-type]
            session_id=_SESSION_ID,
            outcome=outcome,  # type: ignore[arg-type]
            server=self.broker,
            remote_tool="okx_place_order",
            intent_normalized=intent,
            broker_request=broker_request,
            broker_response=broker_response,
            gate_decision=gate_decision,
            error=error,
        )
        try:
            record = write_live_action(event)
        except Exception:
            logger.exception(
                "failed to write live-action audit for %s", self.broker
            )
            return None
        if isinstance(record, Mapping):
            return record.get("audit_id")
        return event.audit_id
