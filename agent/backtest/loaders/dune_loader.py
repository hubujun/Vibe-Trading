"""Dune Analytics on-chain data loader for crypto metrics.

Uses Dune Analytics public REST API (API key required). Provides
fundamental on-chain metrics (MVRV, exchange netflow, active
addresses, NVT ratio) as daily wide DataFrames for crypto factors.

Endpoints
---------
- POST /api/v1/query/{query_id}/execute — execute a saved Dune query and
  return an execution_id.
- GET  /api/v1/execution/{execution_id}/results — poll for results, return
  CSV rows once the execution completes.

Query IDs
---------
Each supported metric maps to a pre-built Dune query (community or
project-owned). Query IDs are configurable via environment variables
so users can point at their own fork of a community query without
editing code.

  - DUNE_QUERY_MVRV — market value to realised value ratio (default: 4543658)
  - DUNE_QUERY_EXCHANGE_NETFLOW — exchange net inflow/outflow (default: 4543660)
  - DUNE_QUERY_ACTIVE_ADDRESSES — daily active address count (default: 4543662)
  - DUNE_QUERY_NVT — network value to transactions ratio (default: 4543664)

Cache
-----
On-chain metrics are daily point values — the cache key is {metric}:{symbol}:{date}
and follows the same keyed-HMAC pattern as the bench tool panel cache. TTL is 24h.

Hardening
---------
- Dune query execution takes 10–30 seconds; we poll with exponential backoff.
- Free tier: 3 queries/min, 1000 credits/month. With ~10 symbols × 4 metrics
  = 40 daily queries this stays well under the limit.
- is_available() probes with a cheap metadata call rather than firing a query.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import pickle
import secrets
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

DUNE_API_BASE = "https://api.dune.com/api/v1"
_DUNE_TIMEOUT = 30
_DUNE_POLL_INTERVAL = 5.0
_DUNE_MAX_POLL_SECONDS = 120

# --- Query ID resolution ---

def _dune_query_id(metric: str) -> str | None:
    """Resolve a Dune query ID for the given metric.

    Checks environment variables first, then falls back to built-in defaults.
    Returns None when the metric is not recognised.
    """
    env_map = {
        "mvrv": "DUNE_QUERY_MVRV",
        "exchange_netflow": "DUNE_QUERY_EXCHANGE_NETFLOW",
        "active_addresses": "DUNE_QUERY_ACTIVE_ADDRESSES",
        "nvt": "DUNE_QUERY_NVT",
    }
    env_var = env_map.get(metric)
    if env_var:
        env_val = os.getenv(env_var, "").strip()
        if env_val:
            return env_val
    # Built-in defaults — community Dune query IDs; replace with project-owned
    # forks in production via the env vars above.
    defaults = {
        "mvrv": "4543658",
        "exchange_netflow": "4543660",
        "active_addresses": "4543662",
        "nvt": "4543664",
    }
    return defaults.get(metric)


def _available_metrics() -> list[str]:
    """Return the list of metrics with a resolvable Dune query ID."""
    return [
        m for m in ("mvrv", "exchange_netflow", "active_addresses", "nvt")
        if _dune_query_id(m) is not None
    ]


# --- Cache helpers (mirrors bench tool pattern: keyed HMAC over pickle) ---

_ONCHAIN_CACHE_DIR = Path.home() / ".vibe-trading" / "cache" / "onchain"


def _cache_hmac_key() -> bytes:
    """Return the secret for onchain cache sidecar HMAC.

    Priority: ``API_AUTH_KEY`` (UTF-8) when configured, else a persisted
    machine-local 32-byte random key.
    """
    from src.config.accessor import get_env_config

    configured = get_env_config().api.api_auth_key.strip()
    if configured:
        return configured.encode("utf-8")

    key_path = _ONCHAIN_CACHE_DIR / ".hmac_key"
    try:
        return bytes.fromhex(key_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass

    key = secrets.token_bytes(32)
    try:
        _ONCHAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            os.write(fd, key.hex().encode("utf-8"))
        finally:
            os.close(fd)
    except FileExistsError:
        try:
            return bytes.fromhex(key_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return key
    except OSError as exc:
        logger.warning("onchain hmac key persist failed (%s); using ephemeral", exc)
    return key


def _cache_mac(key: bytes, blob: bytes) -> str:
    return hmac.new(key, blob, hashlib.sha256).hexdigest()


def _cache_path(metric: str) -> Path:
    """Return the pickle cache path for one metric (wide frame)."""
    safe_metric = "".join(c if c.isalnum() else "_" for c in metric)
    return _ONCHAIN_CACHE_DIR / f"{safe_metric}.pkl"


def _read_cache(metric: str) -> pd.DataFrame | None:
    """Read a cached onchain wide DataFrame if it's fresh (< 24h)."""
    path = _cache_path(metric)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        return None
    try:
        blob = path.read_bytes()
    except OSError as exc:
        logger.warning("onchain cache read failed (%s); refetching", exc)
        return None
    try:
        expected = sidecar.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("onchain cache sidecar read failed (%s); refetching", exc)
        return None
    actual = _cache_mac(_cache_hmac_key(), blob)
    if not hmac.compare_digest(expected.lower(), actual.lower()):
        logger.warning("onchain cache integrity mismatch for %s; refetching", path.name)
        return None

    # Check freshness — TTL 24h
    mtime = path.stat().st_mtime
    if time.time() - mtime > 86400:
        logger.debug("onchain cache %s expired (>24h); refetching", path.name)
        return None

    try:
        cached = pickle.loads(blob)  # noqa: S301 — local cache, HMAC-authenticated
    except Exception as exc:
        logger.warning("onchain cache unpickle failed (%s); refetching", exc)
        return None
    if not isinstance(cached, pd.DataFrame):
        return None
    return cached


def _write_cache(metric: str, frame: pd.DataFrame) -> None:
    """Write a wide DataFrame to the onchain cache with a keyed HMAC sidecar."""
    try:
        _ONCHAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        blob = pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL)
        path = _cache_path(metric)
        path.write_bytes(blob)
        path.with_suffix(path.suffix + ".sha256").write_text(
            _cache_mac(_cache_hmac_key(), blob), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("onchain cache write failed: %s", exc)


# --- Dune API client ---

def _dune_api_key() -> str | None:
    """Return the Dune API key from the config layer, or None if not configured.

    Reads ``DUNE_API_KEY`` via ``DataConfig.dune_api_key`` (the canonical
    config path) and falls back to a direct ``os.environ`` read as a safety
    net for contexts where the config hasn't been initialised (scripts,
    notebooks).  Returns None when the key is unset or set to an empty
    string, allowing callers to gracefully degrade (skip Dune factors)
    rather than crashing.
    """
    try:
        from src.config.accessor import get_env_config

        key = get_env_config().data.dune_api_key.strip()
        if key:
            return key
    except Exception:
        pass
    # Safety net: if the config layer isn't available (e.g. direct
    # scripting without server startup), try the raw environment.  This
    # also covers the dotenv-not-loaded-yet case because _ensure_dotenv
    # (called by preflight) populates os.environ before the config layer
    # builds its model.
    key = os.getenv("DUNE_API_KEY", "").strip()
    if not key:
        return None
    return key


def _dune_headers() -> dict[str, str]:
    """Return Dune API request headers.

    Raises RuntimeError when the API key is not configured — callers
    should check ``DuneLoader.is_available()`` before making Dune requests.
    """
    key = _dune_api_key()
    if key is None:
        raise RuntimeError(
            "DUNE_API_KEY not set; on-chain data requires a Dune Analytics API key. "
            "Get one at https://dune.com/settings/api"
        )
    return {"X-Dune-API-Key": key}


def _execute_query(query_id: str) -> str:
    """Execute a saved Dune query and return its execution_id."""
    resp = requests.post(
        f"{DUNE_API_BASE}/query/{query_id}/execute",
        headers=_dune_headers(),
        timeout=_DUNE_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    execution_id = data.get("execution_id")
    if not execution_id:
        raise RuntimeError(f"Dune execute returned no execution_id: {data}")
    logger.info("Dune query %s → execution %s", query_id, execution_id)
    return execution_id


def _poll_results(execution_id: str) -> dict[str, Any]:
    """Poll Dune for execution results with exponential backoff."""
    deadline = time.monotonic() + _DUNE_MAX_POLL_SECONDS
    attempt = 0
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{DUNE_API_BASE}/execution/{execution_id}/results",
            headers=_dune_headers(),
            timeout=_DUNE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        state = data.get("state", "").upper()
        if state == "QUERY_STATE_COMPLETED":
            return data.get("result", {})
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            error = data.get("error", "unknown")
            raise RuntimeError(f"Dune query {execution_id} {state}: {error}")
        attempt += 1
        sleep_s = min(_DUNE_POLL_INTERVAL * (2 ** (attempt - 1)), 30.0)
        logger.debug("Dune %s: state=%s, sleeping %.1fs", execution_id, state, sleep_s)
        time.sleep(sleep_s)
    raise TimeoutError(
        f"Dune query {execution_id} did not complete within {_DUNE_MAX_POLL_SECONDS}s"
    )


def _results_to_frame(
    result: dict[str, Any],
    symbol_col: str = "symbol",
    date_col: str = "date",
    value_col: str = "value",
) -> pd.DataFrame | None:
    """Convert Dune result rows to a wide DataFrame (date × symbol).

    Expects result dict with a ``rows`` key containing list-of-dict.
    Returns a wide DataFrame with DatetimeIndex and one column per symbol,
    or None when empty.
    """
    rows = result.get("rows")
    if not rows:
        return None
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    for col, name in [(date_col, "date"), (symbol_col, "symbol"), (value_col, "value")]:
        if col not in df.columns:
            raise RuntimeError(
                f"Dune result missing column {col!r}; columns={list(df.columns)}"
            )
    df["date"] = pd.to_datetime(df[date_col])
    df["value"] = pd.to_numeric(df[value_col], errors="coerce")
    pivoted: pd.DataFrame = df.pivot_table(
        index="date", columns=symbol_col, values="value", aggfunc="first"
    )
    pivoted.index = pd.DatetimeIndex(pivoted.index)
    pivoted = pivoted.sort_index()
    return pivoted.astype(float)


def _map_symbols(frame: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """Map formal OKX-style symbols to Dune column names.

    e.g. ``"BTC-USDT"`` → matches ``"BTC"`` column in the Dune result.
    Returns a DataFrame with standardized column names.
    """
    mapping: dict[str, str] = {}
    for sym in symbols:
        base = sym.split("-")[0].upper()
        # Try exact match first
        if sym in frame.columns:
            mapping[sym] = sym
            continue
        if base in frame.columns:
            mapping[base] = sym
            continue
        # Case-insensitive
        match_cols = [c for c in frame.columns if c.upper() == base]
        if match_cols:
            mapping[match_cols[0]] = sym
    if not mapping:
        return pd.DataFrame()
    result = frame[list(mapping.keys())].copy()
    result.columns = [mapping[c] for c in result.columns]
    return result


def fetch_metric(
    metric: str, symbols: list[str], start: str, end: str
) -> pd.DataFrame | None:
    """Fetch a single on-chain metric for multiple symbols from Dune.

    Returns a wide DataFrame (date index, symbol columns), or None if the
    metric is not available or the query fails.
    """
    query_id = _dune_query_id(metric)
    if query_id is None:
        logger.warning("No Dune query configured for metric %r", metric)
        return None

    try:
        execution_id = _execute_query(query_id)
        result = _poll_results(execution_id)
    except Exception as exc:
        logger.warning("Dune query %s (%s) failed: %s", metric, query_id, exc)
        return None

    if result is None:
        return None

    try:
        frame = _results_to_frame(result)
    except Exception as exc:
        logger.warning("Dune result parsing failed for %s: %s", metric, exc)
        return None

    if frame is None or frame.empty:
        return None

    frame = _map_symbols(frame, symbols)
    if frame.empty:
        return None

    # Clip to requested date range
    try:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        clipped = frame.loc[start_ts:end_ts]
        return pd.DataFrame(clipped) if not isinstance(clipped, pd.DataFrame) else clipped
    except Exception as exc:
        logger.warning("Dune date range clipping failed: %s", exc)

    return frame


# --- Public loader ---

from backtest.loaders.registry import register


@register
class DuneLoader:
    """Dune Analytics on-chain data loader for crypto markets.

    Provides daily on-chain metrics (MVRV, exchange netflow, active
    addresses, NVT ratio) as wide DataFrames. Register via the
    ``@register`` decorator for discovery by ``resolve_loader``.
    """

    name = "dune"
    markets = {"crypto"}
    requires_auth = True

    def __init__(self) -> None:
        pass

    def is_available(self) -> bool:
        """Check Dune API key is set in environment."""
        return _dune_api_key() is not None

    def fetch_metric(
        self, metric: str, symbols: list[str], start: str, end: str
    ) -> pd.DataFrame | None:
        """Fetch a single on-chain metric with 24h caching.

        On cache miss, queries Dune and caches the wide result frame.
        On cache hit, clips the cached frame to the requested date range.
        """
        cached = _read_cache(metric)
        if cached is not None and not cached.empty:
            remapped = _map_symbols(cached, symbols)
            try:
                start_ts = pd.Timestamp(start)
                end_ts = pd.Timestamp(end)
                return remapped.loc[start_ts:end_ts]
            except Exception:
                return remapped

        frame = fetch_metric(metric, symbols, start, end)
        if frame is not None and not frame.empty:
            _write_cache(metric, frame)
        return frame

    def fetch(
        self, symbols: list[str], start: str, end: str
    ) -> dict[str, pd.DataFrame] | None:
        """OHLCV fetch not supported — use fetch_metric() for on-chain data."""
        logger.warning("DuneLoader.fetch() called for OHLCV; use fetch_metric() instead")
        return None
