"""Paper-vs-live gap report for the crypto autopilot shadow phase.

While a factor is live (``LIVE_DEPLOYED``), the live executor mirrors
every live fill with a same-signal paper fill (shadow mode).  This
module aggregates the unified trade ledger (``trades.jsonl``, engine
tagged) and the slippage stream (``slippage.jsonl``) into a gap report:
per symbol and per factor, the paper/live fill-price difference in
basis points, the fee difference in USDT, plus the overall slippage
summary.  The API server exposes it at ``/api/autopilot/gap``.

The report is descriptive — nothing here gates trading.  It feeds the
human (and the scale-up decision) with the *measured* cost of going
live: if paper and live diverge, the simulation is lying about
execution quality.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.crypto_autopilot.trade_ledger import (
    read_slippage_records,
    read_trade_records,
)

logger = logging.getLogger(__name__)

__all__ = ["build_gap_report", "GAP_WINDOW_DAYS"]

#: Default report window (calendar days).
GAP_WINDOW_DAYS = 7

#: Ledger records scanned per aggregation pass (newest-first cap).
_LEDGER_SCAN_LIMIT = 10_000


def _now_iso() -> str:
    """Return the current UTC time as ISO-8601 with seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _avg(values: list[float]) -> float | None:
    """Mean of *values*, or ``None`` when the list is empty."""
    if not values:
        return None
    return sum(values) / len(values)


def _gap_bps(paper_avg: float | None, live_avg: float | None) -> float | None:
    """Paper-vs-live average price gap in basis points.

    Positive means the paper fill was *worse* (higher buy price) than
    the live fill; ``None`` when either side has no fills.
    """
    if paper_avg is None or live_avg is None or live_avg == 0:
        return None
    return round((paper_avg - live_avg) / live_avg * 10_000.0, 2)


def _bucket_stats(bucket: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one engine's fills into a stats dict (None-safe)."""
    prices = [b["price"] for b in bucket if b.get("price") is not None]
    fees = [b["fee"] for b in bucket if b.get("fee") is not None]
    return {
        "count": len(bucket),
        "avg_price": round(p, 4) if (p := _avg(prices)) is not None else None,
        "total_fee": round(sum(fees), 4),
    }


def build_gap_report(
    runtime_root: Path,
    days: int = GAP_WINDOW_DAYS,
) -> dict[str, Any]:
    """Build the paper-live gap report over the trailing *days*.

    Args:
        runtime_root: Autopilot runtime root.
        days: Trailing calendar-day window for the aggregation.

    Returns:
        Dict with ``generated_at``, ``window_days``, ``by_symbol``,
        ``by_factor`` and ``slippage`` keys.  Missing files degrade to
        empty sections — the report is a pure read and never raises.
    """
    window = max(1, int(days))
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=window)
    ).replace(microsecond=0).isoformat()

    trades = read_trade_records(runtime_root, limit=_LEDGER_SCAN_LIMIT)
    recent = [t for t in trades if str(t.get("ts", "")) >= cutoff]

    by_symbol: dict[str, dict[str, list[dict[str, Any]]]] = {}
    by_factor: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for trade in recent:
        engine = str(trade.get("engine", ""))
        if engine not in ("paper", "live"):
            continue
        symbol = str(trade.get("symbol", "")).upper()
        factor = str(trade.get("alpha_id") or "unknown")
        entry = {
            "price": trade.get("price"),
            "fee": trade.get("fee"),
            "notional": trade.get("notional"),
        }
        by_symbol.setdefault(symbol, {"paper": [], "live": []})[engine].append(entry)
        by_factor.setdefault(factor, {"paper": [], "live": []})[engine].append(entry)

    symbol_report: dict[str, Any] = {}
    for symbol, buckets in sorted(by_symbol.items()):
        paper = _bucket_stats(buckets["paper"])
        live = _bucket_stats(buckets["live"])
        symbol_report[symbol] = {
            "paper": paper,
            "live": live,
            "price_gap_bps": _gap_bps(paper["avg_price"], live["avg_price"]),
        }

    factor_report: dict[str, Any] = {}
    for factor, buckets in sorted(by_factor.items()):
        paper = _bucket_stats(buckets["paper"])
        live = _bucket_stats(buckets["live"])
        factor_report[factor] = {
            "paper": paper,
            "live": live,
            "price_gap_bps": _gap_bps(paper["avg_price"], live["avg_price"]),
        }

    slippage = read_slippage_records(runtime_root, limit=_LEDGER_SCAN_LIMIT)
    slip_recent = [s for s in slippage if str(s.get("ts", "")) >= cutoff]
    slip_bps = [
        float(s.get("bps", 0.0)) for s in slip_recent
        if s.get("bps") is not None
    ]
    slippage_report: dict[str, Any] = {
        "records": len(slip_recent),
        "avg_bps": (
            round(sum(slip_bps) / len(slip_bps), 2) if slip_bps else None
        ),
        "max_bps": round(max(slip_bps), 2) if slip_bps else None,
    }

    return {
        "generated_at": _now_iso(),
        "window_days": window,
        "by_symbol": symbol_report,
        "by_factor": factor_report,
        "slippage": slippage_report,
    }
