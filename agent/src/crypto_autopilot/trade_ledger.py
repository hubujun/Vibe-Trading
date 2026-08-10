"""Autopilot trade ledger — unified paper/live fill audit stream.

Every fill the autopilot produces — OKX demo fills from
:class:`~src.crypto_autopilot.paper_engine.PaperEngine` and live fills from
:class:`~src.crypto_autopilot.live_executor.LiveExecutor` — is appended as
one JSONL record under ``<runtime_root>/trades.jsonl`` (append-only, ``0700``
tree on first write).  The Shadow Account audit pipeline can then consume a
single engine-tagged fill stream (``engine: "paper" | "live"``) instead of
re-parsing broker-specific formats.

Record fields deliberately overlap :class:`src.tools.trade_journal_parsers.TradeRecord`
(``datetime``/``symbol``/``side``/``quantity``/``price``/``amount``/``fee``/
``market``) so a ledger dump can be fed back into the shadow extractor as a
generic journal; autopilot-specific metadata rides along as extra keys
(``engine``, ``notional``, ``realized_pnl``, ``alpha_id``).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "LEDGER_FILENAME",
    "SLIPPAGE_FILENAME",
    "autopilot_trades_path",
    "autopilot_slippage_path",
    "write_trade_record",
    "read_trade_records",
    "append_slippage_record",
    "read_slippage_records",
]

#: Ledger file name inside the autopilot runtime root.
LEDGER_FILENAME = "trades.jsonl"

#: Slippage measurement file name inside the autopilot runtime root.
SLIPPAGE_FILENAME = "slippage.jsonl"

#: Default cap for ``read_trade_records`` so one scan cannot blow up memory.
_MAX_READ_LIMIT = 10_000

#: Ledger keys that map 1:1 onto ``TradeRecord`` (Shadow Account journal).
_TRADERECORD_KEYS = ("ts", "symbol", "side", "quantity", "price", "notional")


def autopilot_trades_path(runtime_root: Path) -> Path:
    """Return the ledger path for *runtime_root*.

    Args:
        runtime_root: Autopilot runtime root (``<agent>/runs/autopilot``).

    Returns:
        ``<runtime_root>/trades.jsonl``.  Neither the file nor its parent is
        created here; :func:`write_trade_record` creates the tree on first
        append.
    """
    return Path(runtime_root) / LEDGER_FILENAME


def autopilot_slippage_path(runtime_root: Path) -> Path:
    """Return the slippage measurement path for *runtime_root*.

    Args:
        runtime_root: Autopilot runtime root (``<agent>/runs/autopilot``).

    Returns:
        ``<runtime_root>/slippage.jsonl``.  Neither the file nor its parent
        is created here; :func:`append_slippage_record` creates the tree on
        first append.
    """
    return Path(runtime_root) / SLIPPAGE_FILENAME


def _now_iso() -> str:
    """Return the current UTC time as ISO-8601 with seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_trade_record(
    runtime_root: Path,
    *,
    engine: str,
    symbol: str,
    side: str,
    notional: float,
    quantity: float | None = None,
    price: float | None = None,
    realized_pnl: float | None = None,
    fee: float | None = None,
    alpha_id: str | None = None,
    ts: str | None = None,
) -> dict[str, Any] | None:
    """Append one fill record to the autopilot trade ledger.

    Best-effort: a ledger write failure is logged and swallowed — auditing
    must never block a fill or the trading loop.

    Args:
        runtime_root: Autopilot runtime root.
        engine: ``"paper"`` or ``"live"``.
        symbol: Instrument id, e.g. ``"BTC-USDT"``.
        side: ``"buy"`` or ``"sell"``.
        notional: Quote-currency amount (USD) of the fill.
        quantity: Filled base-currency quantity (``None`` when unknown).
        price: Fill price (``None`` when unknown).
        realized_pnl: Realized P&L when this fill closed a position.
        fee: Quote-currency trading fee of the fill (``None`` when unknown,
            recorded as 0.0 for Shadow Account compatibility).
        alpha_id: Factor id that triggered the fill, if any.
        ts: ISO-8601 UTC timestamp; defaults to now.

    Returns:
        The written record dict, or ``None`` on write failure.
    """
    record: dict[str, Any] = {
        "ts": ts or _now_iso(),
        "engine": engine,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "notional": float(notional),
        "realized_pnl": realized_pnl,
        "alpha_id": alpha_id,
        # TradeRecord-compatible extras for Shadow Account ingestion.
        "name": symbol,
        "fee": fee if fee is not None else 0.0,
        "market": "crypto",
    }
    try:
        path = autopilot_trades_path(runtime_root)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(record, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        logger.warning("trade ledger append failed: %s", exc)
        return None
    return record


def read_trade_records(
    runtime_root: Path,
    *,
    limit: int = 100,
    engine: str | None = None,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Read the autopilot trade ledger, newest first.

    Args:
        runtime_root: Autopilot runtime root.
        limit: Maximum records to return (clamped to ``_MAX_READ_LIMIT``).
        engine: Filter by engine (``"paper"`` / ``"live"``); ``None`` = all.
        symbol: Filter by exact symbol (case-insensitive); ``None`` = all.

    Returns:
        Newest-first list of record dicts.  Corrupt lines are skipped
        (logged at debug level) so one bad line cannot hide the rest.
    """
    path = autopilot_trades_path(runtime_root)
    if not path.is_file():
        return []

    limit = max(0, min(int(limit), _MAX_READ_LIMIT))
    symbol = symbol.strip().upper() if symbol else None
    engine = engine.strip().lower() if engine else None

    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    logger.debug("trade ledger: skipping corrupt line")
                    continue
                if engine is not None and record.get("engine") != engine:
                    continue
                if symbol is not None and str(record.get("symbol", "")).upper() != symbol:
                    continue
                records.append(record)
    except OSError as exc:
        logger.warning("trade ledger read failed: %s", exc)
        return []

    records.reverse()  # newest first
    return records[:limit]


def append_slippage_record(
    runtime_root: Path,
    *,
    symbol: str,
    signal_price: float,
    fill_price: float,
    ts: str | None = None,
) -> dict[str, Any] | None:
    """Append one slippage measurement to the JSONL stream.

    The spread between the signal price (last close when the decision was
    made) and the actual fill price is recorded in basis points; a positive
    ``bps`` means the fill was worse than the signal price.

    Best-effort: a write failure is logged and swallowed — measurement
    must never block a fill.

    Args:
        runtime_root: Autopilot runtime root.
        symbol: Instrument id, e.g. ``"BTC-USDT"``.
        signal_price: Price the strategy observed when it decided to trade.
        fill_price: Price the order actually filled at.
        ts: ISO-8601 UTC timestamp; defaults to now.

    Returns:
        The written record dict, or ``None`` on write failure.
    """
    signal = float(signal_price)
    fill = float(fill_price)
    record: dict[str, Any] = {
        "ts": ts or _now_iso(),
        "symbol": symbol,
        "signal_price": signal,
        "fill_price": fill,
        # Guard against a zero/absent signal price: report 0 bps rather than
        # raising — measurement must never block a fill.
        "bps": round((fill - signal) / signal * 10_000.0, 2) if signal > 0 else 0.0,
    }
    try:
        path = autopilot_slippage_path(runtime_root)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(record, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        logger.warning("slippage append failed: %s", exc)
        return None
    return record


def read_slippage_records(
    runtime_root: Path,
    *,
    limit: int = 1000,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Read slippage measurements, newest first.

    Args:
        runtime_root: Autopilot runtime root.
        limit: Maximum records to return (clamped to ``_MAX_READ_LIMIT``).
        symbol: Filter by exact symbol (case-insensitive); ``None`` = all.

    Returns:
        Newest-first list of record dicts.  Corrupt lines are skipped
        (logged at debug level).
    """
    path = autopilot_slippage_path(runtime_root)
    if not path.is_file():
        return []

    limit = max(0, min(int(limit), _MAX_READ_LIMIT))
    symbol = symbol.strip().upper() if symbol else None

    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    logger.debug("slippage: skipping corrupt line")
                    continue
                if symbol is not None and str(record.get("symbol", "")).upper() != symbol:
                    continue
                records.append(record)
    except OSError as exc:
        logger.warning("slippage read failed: %s", exc)
        return []

    records.reverse()  # newest first
    return records[:limit]
