"""Staged live order sizing for the crypto autopilot — paper → live ramp.

Live trading starts at the smallest tier (``live_order_scale``, default
$5) and advances one tier at a time along the fixed
:data:`SCALE_TIERS` ladder — never beyond ``live_scale_max_usd``.  A tier
advance requires ``live_scale_up_days`` consecutive clean trading days
(every recent measured day's average slippage under
``live_scale_up_max_slippage_bps``, 20 bps by default) **and** no active
halt.  The ladder is hard-coded and the advance conditions are
deliberately conservative: there is no automatic unlimited scaling, and
a bad day resets the consecutive-day counter.

The persisted state lives in ``<runtime_root>/live_scale.json`` so the
tier survives process restarts (launchd keep-alive) and is observable
from the API server's gap endpoint.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.crypto_autopilot.config import SCALE_TIERS, AutopilotConfig
from src.crypto_autopilot.trade_ledger import read_slippage_records

logger = logging.getLogger(__name__)

__all__ = [
    "SCALE_FILENAME",
    "live_scale_path",
    "load_live_scale",
    "save_live_scale",
    "current_live_scale",
    "next_tier",
    "recent_clean_days",
    "maybe_scale_up",
]

#: Persisted scale-state file name inside the autopilot runtime root.
SCALE_FILENAME = "live_scale.json"

#: Records scanned when aggregating daily slippage (newest-first cap).
_SLIPPAGE_SCAN_LIMIT = 10_000


def _now_iso() -> str:
    """Return the current UTC time as ISO-8601 with seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def live_scale_path(runtime_root: Path) -> Path:
    """Return the scale-state path for *runtime_root*.

    Returns:
        ``<runtime_root>/live_scale.json``.  The file is created by
        :func:`save_live_scale` on first write; a missing file simply
        means "still on the initial tier".
    """
    return Path(runtime_root) / SCALE_FILENAME


def load_live_scale(
    runtime_root: Path,
    initial: float | None = None,
) -> dict[str, Any]:
    """Load the persisted live-order scale state.

    Args:
        runtime_root: Autopilot runtime root.
        initial: Initial tier value when no state exists; defaults to
            :data:`SCALE_TIERS` index 0 (``live_order_scale`` of the
            config is normally passed here).

    Returns:
        Dict with ``scale``, ``tier_index``, ``since``,
        ``last_scale_up`` and ``last_checked`` keys.  A corrupt or
        missing file degrades to the initial tier (never raises).
    """
    start = SCALE_TIERS[0] if initial is None else float(initial)
    path = live_scale_path(runtime_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {
            "scale": start,
            "tier_index": 0,
            "since": _now_iso(),
            "last_scale_up": None,
            "last_checked": None,
        }
    try:
        return {
            "scale": float(raw.get("scale", start)),
            "tier_index": int(raw.get("tier_index", 0)),
            "since": str(raw.get("since") or _now_iso()),
            "last_scale_up": raw.get("last_scale_up"),
            "last_checked": raw.get("last_checked"),
        }
    except (TypeError, ValueError):
        return {
            "scale": start,
            "tier_index": 0,
            "since": _now_iso(),
            "last_scale_up": None,
            "last_checked": None,
        }


def save_live_scale(
    runtime_root: Path,
    *,
    scale: float,
    tier_index: int,
    since: str | None = None,
    last_scale_up: str | None = None,
) -> dict[str, Any]:
    """Persist the live-order scale state (best-effort).

    Args:
        runtime_root: Autopilot runtime root.
        scale: Current tier notional (USD).
        tier_index: Index into :data:`SCALE_TIERS`.
        since: ISO timestamp the current tier started.
        last_scale_up: ISO timestamp of the last tier advance.

    Returns:
        The persisted state dict; write failures are logged and the
        state is still returned so the caller can proceed in-memory.
    """
    state = {
        "scale": float(scale),
        "tier_index": int(tier_index),
        "since": since or _now_iso(),
        "last_scale_up": last_scale_up,
        "last_checked": _now_iso(),
    }
    try:
        path = live_scale_path(runtime_root)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("live scale state write failed: %s", exc)
    return state


def current_live_scale(runtime_root: Path, initial: float | None = None) -> float:
    """Return the current live order notional (USD) from persisted state."""
    return float(load_live_scale(runtime_root, initial=initial)["scale"])


def next_tier(scale: float) -> float | None:
    """Return the next :data:`SCALE_TIERS` tier above *scale*, or ``None``."""
    for tier in SCALE_TIERS:
        if tier > scale + 1e-9:
            return float(tier)
    return None


def recent_clean_days(
    runtime_root: Path,
    config: AutopilotConfig,
) -> tuple[list[str], dict[str, Any]]:
    """Aggregate daily slippage and count the trailing clean run.

    A day is *clean* when it has at least one slippage measurement and
    the day's average |bps| is under ``live_scale_up_max_slippage_bps``.
    The count walks backwards from the most recent measured day and
    stops at the first non-clean (or missing) day, so any bad day resets
    the streak.

    Args:
        runtime_root: Autopilot runtime root.
        config: Autopilot config carrying the slippage threshold.

    Returns:
        ``(clean_day_list_oldest_first, daily_summary)`` where
        ``daily_summary`` maps ``YYYY-MM-DD`` to
        ``{"avg_bps": ..., "records": n}`` for every measured day.
    """
    threshold = config.live_scale_up_max_slippage_bps
    records = read_slippage_records(
        runtime_root, limit=_SLIPPAGE_SCAN_LIMIT,
    )  # newest first
    per_day: dict[str, list[float]] = {}
    for record in records:
        day = str(record.get("ts", ""))[:10]
        if not day:
            continue
        try:
            bps = float(record.get("bps", 0.0))
        except (TypeError, ValueError):
            continue
        per_day.setdefault(day, []).append(bps)

    summary: dict[str, Any] = {}
    for day in sorted(per_day):  # oldest first
        values = per_day[day]
        avg = sum(values) / len(values)
        summary[day] = {"avg_bps": round(avg, 2), "records": len(values)}

    # Trailing run: walk the measured days newest-first, keep only the
    # contiguous clean tail (any bad day resets the streak).
    tail: list[str] = []
    for day in reversed(sorted(per_day)):
        if abs(summary[day]["avg_bps"]) < threshold:
            tail.append(day)
        else:
            break
    return tail, summary


def maybe_scale_up(
    runtime_root: Path,
    config: AutopilotConfig,
    *,
    halt_active: bool = False,
) -> dict[str, Any]:
    """Advance the live order scale one tier when the conditions hold.

    Conditions (all must hold):
    - the current tier is below ``live_scale_max_usd``;
    - no halt is active (``halt_active=False``);
    - the last ``live_scale_up_days`` measured days are all clean
      (average daily slippage under ``live_scale_up_max_slippage_bps``).

    The result is persisted to ``live_scale.json`` so the new tier
    survives restarts.  Any evaluation failure degrades to "no scale
    up" — the ladder never advances on missing or ambiguous evidence.

    Args:
        runtime_root: Autopilot runtime root.
        config: Autopilot config with the scale-up knobs.
        halt_active: Whether the live broker is currently halted.

    Returns:
        Dict with ``scaled_up``, ``scale``, ``old_scale``, ``reason``,
        ``clean_days`` and ``required_days`` keys.
    """
    state = load_live_scale(runtime_root, initial=config.live_order_scale)
    scale = float(state["scale"])

    if scale >= config.live_scale_max_usd - 1e-9:
        return {
            "scaled_up": False,
            "scale": scale,
            "old_scale": scale,
            "reason": "already at max scale",
            "clean_days": None,
            "required_days": config.live_scale_up_days,
        }
    if halt_active:
        return {
            "scaled_up": False,
            "scale": scale,
            "old_scale": scale,
            "reason": "halt active",
            "clean_days": None,
            "required_days": config.live_scale_up_days,
        }

    tail, summary = recent_clean_days(runtime_root, config)
    if len(tail) < config.live_scale_up_days:
        return {
            "scaled_up": False,
            "scale": scale,
            "old_scale": scale,
            "reason": (
                f"need {config.live_scale_up_days} consecutive clean days, "
                f"have {len(tail)}"
            ),
            "clean_days": len(tail),
            "required_days": config.live_scale_up_days,
            "daily": summary,
        }

    tier = next_tier(scale)
    if tier is None:
        return {
            "scaled_up": False,
            "scale": scale,
            "old_scale": scale,
            "reason": "already at max tier",
            "clean_days": len(tail),
            "required_days": config.live_scale_up_days,
        }
    now = _now_iso()
    new_state = save_live_scale(
        runtime_root,
        scale=tier,
        tier_index=SCALE_TIERS.index(tier),
        since=now,
        last_scale_up=now,
    )
    logger.info(
        "live scale up: $%.0f → $%.0f after %d clean days",
        scale, tier, len(tail),
    )
    return {
        "scaled_up": True,
        "scale": tier,
        "old_scale": scale,
        "reason": f"{len(tail)} consecutive clean days",
        "clean_days": len(tail),
        "required_days": config.live_scale_up_days,
        "daily": summary,
        "state": new_state,
    }
