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

__all__ = ["AutopilotConfig", "load_autopilot_config", "SCALE_TIERS"]

#: Default trading pairs for the crypto autopilot loop. A broad universe
#: gives the factor screen a cross-section to test consistency over;
#: mined factors must hold across assets, not just one pair.
#: (统一为 OKB 版 — 与策略引擎 combo/daily_signal 的 universe 一致;
#:  OKB 为 OKX 平台币, 老胡交易生态相关)
_DEFAULT_PAIRS: tuple[str, ...] = (
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "BNB-USDT",
    "XRP-USDT",
    "DOGE-USDT",
    "ADA-USDT",
    "AVAX-USDT",
    "LINK-USDT",
    "OKB-USDT",
    "LTC-USDT",
    "DOT-USDT",
    "UNI-USDT",
    "APT-USDT",
    "ARB-USDT",
    "TRUMP-USDT",
    "LAB-USDT",
)


@dataclass(frozen=True)
class AutopilotConfig:
    """All tunable parameters for the crypto_autopilot 24/7 loop.

    Attributes:
        enabled: Master switch (``AUTOPILOT_ENABLED``). Default ``False`` —
            the autopilot never starts unless explicitly opted in.
        pairs: Trading pairs to mine and trade (``AUTOPILOT_PAIRS``),
            comma-separated in env. Default 10 major USDT pairs.
        max_order_notional_usd: Max single-order notional, USD. Default 25
            (calibrated for a $500 paper account — 5% per order).
        max_total_exposure_usd: Max aggregate open exposure, USD. Default 200.
        max_trades_per_day: Max order placements per UTC day. Default 10.
        trade_cooldown_minutes: Minimum minutes between paper-trade orders
            (global gate; signal-gated ticks stay inside this cadence).
            Default 30.
        take_profit_usd: Realized USD gain that closes a position. Default 5.
        stop_loss_usd: Realized USD loss that closes a position. Default -5.
        max_holding_hours: Max hours a position stays open before forced
            exit. Default 24.
        paper_min_days: Minimum paper-trading duration before live promotion,
            in days. Default 14.
        kill_loss_pct: Drawdown percentage that trips the kill switch.
            Default 5.0 ($500 account = single-day -$25 circuit breaker).
        live_order_scale: Initial live order notional, USD — the lowest tier
            of the staged scale-up (``AUTOPILOT_LIVE_ORDER_SCALE``). Default 5.
        live_scale_max_usd: Ceiling of the live scale-up ladder
            (``AUTOPILOT_LIVE_SCALE_MAX_USD``). Default 50 — the ladder never
            scales beyond this.
        live_scale_up_days: Consecutive clean days (slippage under
            ``live_scale_up_max_slippage_bps`` and no halt) required before
            the live order scale advances one tier
            (``AUTOPILOT_LIVE_SCALE_UP_DAYS``). Default 7.
        live_scale_up_max_slippage_bps: Max average daily slippage (bps)
            that counts as a clean day for scaling up
            (``AUTOPILOT_LIVE_SCALE_UP_MAX_SLIPPAGE_BPS``). Default 20.
        live_shadow_enabled: When True the live executor mirrors every live
            fill with a same-signal paper fill so the paper-live gap report
            can compare them (``AUTOPILOT_LIVE_SHADOW_ENABLED``). Default True.
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
        target_annual_return: Annual return target (fraction) used as the
            success benchmark in feedback and monitoring. Default 0.15.
        benchmark_symbol: Buy-and-hold benchmark instrument for relative
            performance (``AUTOPILOT_BENCHMARK_SYMBOL``). Default
            ``"BTC-USDT"``.
        bar_period: K-line period for the data panel (``AUTOPILOT_BAR_PERIOD``).
            Hourly bars accumulate 24 samples/day — far faster factor
            cold-start than daily bars. Default ``"1h"``.
        bar_limit: Bars per symbol in the data panel (``AUTOPILOT_BAR_LIMIT``).
            Default 180 (~7.5 days of hourly data).
        history_days: Days of historical bars kept in the local history store
            for long-window evaluation backtests (``AUTOPILOT_HISTORY_DAYS``).
            Default 365.
        eval_bars: Bars per symbol used when evaluating candidates against
            the history store (``AUTOPILOT_EVAL_BARS``). Default 1440
            (60 days of hourly data).
        fee_rate_taker: Taker fee rate (fraction of notional) charged on
            every fill when the broker response does not report a fee
            (``AUTOPILOT_FEE_RATE_TAKER``). Default 0.0008 (0.08%).
        max_factor_correlation: |IC-series correlation| above which a
            candidate factor is rejected as a duplicate of an active factor
            (``AUTOPILOT_MAX_FACTOR_CORRELATION``). Default 0.7.
        max_single_factor_weight: Cap on one factor's share of the per-tick
            order notional when weighting by |IC| (``AUTOPILOT_MAX_SINGLE_FACTOR_WEIGHT``).
            Default 0.5; more than 3 active factors falls back to equal weight.
    """

    enabled: bool = False
    pairs: list[str] = field(default_factory=lambda: list(_DEFAULT_PAIRS))
    max_order_notional_usd: float = 25.0
    max_total_exposure_usd: float = 200.0
    max_trades_per_day: int = 10
    trade_cooldown_minutes: int = 30
    take_profit_usd: float = 5.0
    stop_loss_usd: float = -5.0
    max_holding_hours: int = 24
    paper_min_days: int = 14
    kill_loss_pct: float = 5.0
    live_order_scale: float = 5.0
    live_scale_max_usd: float = 50.0
    live_scale_up_days: int = 7
    live_scale_up_max_slippage_bps: float = 20.0
    live_shadow_enabled: bool = True
    mine_interval_hours: int = 6
    evaluate_interval_hours: int = 1
    trade_interval_minutes: int = 5
    feedback_interval_hours: int = 6
    deepseek_model: str = "deepseek-chat"
    bars_per_year: int = 365
    paper_profile: str = "paper"
    live_profile: str = "live"
    target_annual_return: float = 0.15
    benchmark_symbol: str = "BTC-USDT"
    paper_simulated: bool = False
    bar_period: str = "1h"
    bar_limit: int = 180
    history_days: int = 365
    eval_bars: int = 1440
    fee_rate_taker: float = 0.0008
    max_factor_correlation: float = 0.7
    max_single_factor_weight: float = 0.5


SCALE_TIERS = (5.0, 10.0, 25.0, 50.0)


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


def _parse_bool_default(value: str | None, default: bool) -> bool:
    """Parse an env bool, falling back to *default* when unset/blank.

    Mirrors :func:`src.config.accessor._parse_bool` but keeps a True
    default possible (shadow mode is on unless explicitly disabled).
    """
    if value is None or value.strip() == "":
        return default
    return _parse_bool(value)


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
        max_order_notional_usd=_parse_float("AUTOPILOT_MAX_ORDER_NOTIONAL_USD", 25.0),
        max_total_exposure_usd=_parse_float("AUTOPILOT_MAX_TOTAL_EXPOSURE_USD", 200.0),
        max_trades_per_day=_parse_int("AUTOPILOT_MAX_TRADES_PER_DAY", 10),
        trade_cooldown_minutes=_parse_int("AUTOPILOT_TRADE_COOLDOWN_MINUTES", 30),
        take_profit_usd=_parse_float("AUTOPILOT_TAKE_PROFIT_USD", 5.0),
        stop_loss_usd=_parse_float("AUTOPILOT_STOP_LOSS_USD", -5.0),
        max_holding_hours=_parse_int("AUTOPILOT_MAX_HOLDING_HOURS", 24),
        paper_min_days=_parse_int("AUTOPILOT_PAPER_MIN_DAYS", 14),
        kill_loss_pct=_parse_float("AUTOPILOT_KILL_LOSS_PCT", 5.0),
        live_order_scale=_parse_float("AUTOPILOT_LIVE_ORDER_SCALE", 5.0),
        live_scale_max_usd=_parse_float("AUTOPILOT_LIVE_SCALE_MAX_USD", 50.0),
        live_scale_up_days=_parse_int("AUTOPILOT_LIVE_SCALE_UP_DAYS", 7),
        live_scale_up_max_slippage_bps=_parse_float(
            "AUTOPILOT_LIVE_SCALE_UP_MAX_SLIPPAGE_BPS", 20.0,
        ),
        live_shadow_enabled=_parse_bool_default(
            os.getenv("AUTOPILOT_LIVE_SHADOW_ENABLED"), True,
        ),
        mine_interval_hours=_parse_int("AUTOPILOT_MINE_INTERVAL_HOURS", 6),
        evaluate_interval_hours=_parse_int("AUTOPILOT_EVALUATE_INTERVAL_HOURS", 1),
        trade_interval_minutes=_parse_int("AUTOPILOT_TRADE_INTERVAL_MINUTES", 5),
        feedback_interval_hours=_parse_int("AUTOPILOT_FEEDBACK_INTERVAL_HOURS", 6),
        deepseek_model=os.getenv("AUTOPILOT_DEEPSEEK_MODEL", "deepseek-chat"),
        bars_per_year=_parse_int("AUTOPILOT_BARS_PER_YEAR", 365),
        paper_profile=os.getenv("AUTOPILOT_PAPER_PROFILE", "paper"),
        live_profile=os.getenv("AUTOPILOT_LIVE_PROFILE", "live"),
        target_annual_return=_parse_float("AUTOPILOT_TARGET_ANNUAL_RETURN", 0.15),
        benchmark_symbol=os.getenv("AUTOPILOT_BENCHMARK_SYMBOL", "BTC-USDT"),
        paper_simulated=_parse_bool(os.getenv("AUTOPILOT_PAPER_SIMULATED")),
        bar_period=os.getenv("AUTOPILOT_BAR_PERIOD", "1h"),
        bar_limit=_parse_int("AUTOPILOT_BAR_LIMIT", 180),
        history_days=_parse_int("AUTOPILOT_HISTORY_DAYS", 365),
        eval_bars=_parse_int("AUTOPILOT_EVAL_BARS", 1440),
        fee_rate_taker=_parse_float("AUTOPILOT_FEE_RATE_TAKER", 0.0008),
        max_factor_correlation=_parse_float("AUTOPILOT_MAX_FACTOR_CORRELATION", 0.7),
        max_single_factor_weight=_parse_float("AUTOPILOT_MAX_SINGLE_FACTOR_WEIGHT", 0.5),
    )
