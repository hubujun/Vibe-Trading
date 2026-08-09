"""Centralised configuration for the crypto_autopilot module.

A frozen :class:`AutopilotConfig` dataclass holds every tunable knob for the
24/7 autonomous trading loop. :func:`load_autopilot_config` reads the
``AUTOPILOT_*`` environment variables and applies documented defaults —
matching the env-var-driven style of :mod:`src.config.env_schema` while
keeping the autopilot module self-contained (it does not register fields on
the root :class:`~src.config.env_schema.EnvConfig` yet; future phases may
fold it in).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.config.accessor import _parse_bool

__all__ = ["AutopilotConfig", "load_autopilot_config"]

#: Default trading pairs for the crypto autopilot loop.
_DEFAULT_PAIRS: tuple[str, ...] = ("BTC-USDT", "ETH-USDT")


@dataclass(frozen=True)
class AutopilotConfig:
    """All tunable parameters for the crypto_autopilot 24/7 loop.

    Attributes:
        enabled: Master switch (``AUTOPILOT_ENABLED``). Default ``False`` —
            the autopilot never starts unless explicitly opted in.
        pairs: Trading pairs to mine and trade (``AUTOPILOT_PAIRS``),
            comma-separated in env. Default ``["BTC-USDT", "ETH-USDT"]``.
        max_order_notional_usd: Max single-order notional, USD. Default 50.
        max_total_exposure_usd: Max aggregate open exposure, USD. Default 200.
        max_trades_per_day: Max order placements per UTC day. Default 10.
        paper_min_days: Minimum paper-trading duration before live promotion,
            in days. Default 14.
        kill_loss_pct: Drawdown percentage that trips the kill switch.
            Default 5.0.
        mine_interval_hours: Hours between mining cycles. Default 6.
        evaluate_interval_hours: Hours between factor evaluation passes.
            Default 1.
        trade_interval_minutes: Minutes between trade-execution ticks.
            Default 5.
        feedback_interval_hours: Hours between feedback/reflection cycles.
            Default 6.
        deepseek_model: LLM model name for mining and evaluation prompts.
            Default ``"deepseek-chat"`` (adjust to the production model later).
        bars_per_year: Annualisation factor for Sharpe etc. Crypto trades
            24/7, so daily bars use 365. Default 365.
        paper_profile: Shadow-account profile name for paper trading.
            Default ``"paper"``.
        live_profile: Shadow-account profile name for live trading.
            Default ``"live"``.
    """

    enabled: bool = False
    pairs: list[str] = field(default_factory=lambda: list(_DEFAULT_PAIRS))
    max_order_notional_usd: float = 50.0
    max_total_exposure_usd: float = 200.0
    max_trades_per_day: int = 10
    paper_min_days: int = 14
    kill_loss_pct: float = 5.0
    mine_interval_hours: int = 6
    evaluate_interval_hours: int = 1
    trade_interval_minutes: int = 5
    feedback_interval_hours: int = 6
    deepseek_model: str = "deepseek-chat"
    bars_per_year: int = 365
    paper_profile: str = "paper"
    live_profile: str = "live"


def _parse_int(name: str, default: int) -> int:
    """Read env var *name* as int, falling back to *default* on error.

    Mirrors the lenient coercion in :class:`src.config.env_schema._EnvBase`:
    an unparseable value is silently dropped so the field default applies.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_float(name: str, default: float) -> float:
    """Read env var *name* as float, falling back to *default* on error."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_pairs(raw: str | None) -> list[str]:
    """Parse a comma-separated pair list from an env-var string.

    Args:
        raw: The raw env value (e.g. ``"BTC-USDT,ETH-USDT"``).

    Returns:
        A list of trimmed, non-empty pair strings.
    """
    if not raw:
        return list(_DEFAULT_PAIRS)
    return [p.strip() for p in raw.split(",") if p.strip()]


def load_autopilot_config() -> AutopilotConfig:
    """Build an :class:`AutopilotConfig` from ``AUTOPILOT_*`` env vars.

    Unset variables fall back to the dataclass defaults. Invalid numeric
    values fall back to defaults rather than raising — matching the lenient
    coercion in :class:`src.config.env_schema._EnvBase`.

    Returns:
        A frozen :class:`AutopilotConfig` populated from the environment.
    """
    return AutopilotConfig(
        enabled=_parse_bool(os.getenv("AUTOPILOT_ENABLED")),
        pairs=_parse_pairs(os.getenv("AUTOPILOT_PAIRS")),
        max_order_notional_usd=_parse_float("AUTOPILOT_MAX_ORDER_NOTIONAL_USD", 50.0),
        max_total_exposure_usd=_parse_float("AUTOPILOT_MAX_TOTAL_EXPOSURE_USD", 200.0),
        max_trades_per_day=_parse_int("AUTOPILOT_MAX_TRADES_PER_DAY", 10),
        paper_min_days=_parse_int("AUTOPILOT_PAPER_MIN_DAYS", 14),
        kill_loss_pct=_parse_float("AUTOPILOT_KILL_LOSS_PCT", 5.0),
        mine_interval_hours=_parse_int("AUTOPILOT_MINE_INTERVAL_HOURS", 6),
        evaluate_interval_hours=_parse_int("AUTOPILOT_EVALUATE_INTERVAL_HOURS", 1),
        trade_interval_minutes=_parse_int("AUTOPILOT_TRADE_INTERVAL_MINUTES", 5),
        feedback_interval_hours=_parse_int("AUTOPILOT_FEEDBACK_INTERVAL_HOURS", 6),
        deepseek_model=os.getenv("AUTOPILOT_DEEPSEEK_MODEL", "deepseek-chat"),
        bars_per_year=_parse_int("AUTOPILOT_BARS_PER_YEAR", 365),
        paper_profile=os.getenv("AUTOPILOT_PAPER_PROFILE", "paper"),
        live_profile=os.getenv("AUTOPILOT_LIVE_PROFILE", "live"),
    )
