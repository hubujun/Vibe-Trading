"""OKX spot candle loader (crypto).

Uses OKX V5 public REST API (no auth).

Endpoints
---------
- ``/market/candles`` — recent bars only (limited depth; not enough for multi-year
  backtests).
- ``/market/history-candles`` — multi-year history; used whenever the requested
  range is older than a few months or recent endpoint returns empty.
- ``/public/funding-rate-history`` — historical perpetual swap funding rates.
- ``/public/open-interest`` — current open interest snapshot.

Hardening (2026-07 local audit)
--------------------------------
- Explicit proxy support (same env vars as CCXT loader); required on networks
  that time out direct HTTPS to www.okx.com.
- Raise / retry on HTTP 429/5xx and OKX business ``code != "0"``.
- Prefer ``history-candles`` for deep ranges so 2020-era backtests no longer
  return empty frames.
- ``is_available()`` does a short probe instead of always returning True.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# Project / connector period tokens -> OKX candle ``bar`` strings.
# ``1m`` vs ``1M`` stays case-sensitive; hour/day accept either case.
_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "1H": "1H",
    "4h": "4H",
    "4H": "4H",
    "1d": "1D",
    "1D": "1D",
}

from backtest.loaders.base import (
    cached_loader_fetch,
    check_budget,
    positive_env_float,
    positive_env_int,
    retry_with_budget,
    validate_date_range,
)
from backtest.loaders.registry import register

BASE_URL = "https://www.okx.com/api/v5"
CANDLES_PATH = f"{BASE_URL}/market/candles"
HISTORY_CANDLES_PATH = f"{BASE_URL}/market/history-candles"
FUNDING_RATE_PATH = f"{BASE_URL}/public/funding-rate-history"
OPEN_INTEREST_PATH = f"{BASE_URL}/public/open-interest"
_MAX_PER_PAGE = 300
_MAX_FR_PER_PAGE = 100
# Recent endpoint typically only covers ~months of 1D bars; beyond this age
# always hit history-candles first.
_RECENT_ONLY_DAYS = 400

_OKX_TIMEOUT = positive_env_int("OKX_TIMEOUT_S", 20)
_OKX_FETCH_BUDGET_S = positive_env_float("OKX_FETCH_BUDGET_S", 90.0)
_OKX_PROBE_TIMEOUT = positive_env_int("OKX_PROBE_TIMEOUT_S", 8)

# OKX funding-rate-history only retains ~95 days of data.  When the requested
# range starts before that window, the `after`-based pagination starts in a
# region the API no longer serves and the first page comes back empty.  Set
# this flag to True in the DataLoader to skip the `after` param on the first
# call so the loader picks up whatever recent data the API still holds.
_FR_HISTORY_UNSAFE = True


def _first_proxy_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()  # noqa: env-gate — system proxy vars
        if value:
            return value
    return ""


def _okx_proxy_config() -> dict[str, str]:
    """Build requests proxies from conventional env vars (parity with CCXT)."""
    all_proxy = _first_proxy_env("ALL_PROXY", "all_proxy")
    http_proxy = _first_proxy_env("HTTP_PROXY", "http_proxy") or all_proxy
    https_proxy = _first_proxy_env("HTTPS_PROXY", "https_proxy") or all_proxy or http_proxy
    proxies: dict[str, str] = {}
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    return proxies


def _okx_session() -> requests.Session:
    session = requests.Session()
    proxies = _okx_proxy_config()
    if proxies:
        session.proxies.update(proxies)
    return session


def _to_swap_inst_id(symbol: str) -> str:
    """Convert spot ``BTC-USDT`` -> perpetual swap ``BTC-USDT-SWAP``."""
    upper = symbol.upper().replace("/", "-")
    if upper.endswith("-SWAP"):
        return upper
    return upper + "-SWAP"


def _to_ccxt_swap_symbol(symbol: str) -> str:
    """Convert ``BTC-USDT`` (or ``BTC/USDT``) -> CCXT OKX swap ``BTC/USDT:USDT``.

    CCXT represents OKX USDT-margined perpetual swaps as ``BASE/QUOTE:QUOTE``
    (e.g. ``BTC/USDT:USDT``, ``ETH/USDT:USDT``). This helper normalises our
    internal symbol into that format.
    """
    upper = symbol.upper().replace("-", "/")
    # Strip trailing -SWAP suffix if present (e.g. "BTC/USDT-SWAP" → "BTC/USDT")
    if upper.endswith("/SWAP"):
        upper = upper[:-5]
    if ":" in upper:
        return upper
    parts = upper.split("/")
    if len(parts) != 2:
        return f"{upper}:{upper}"
    return f"{parts[0]}/{parts[1]}:{parts[1]}"


@register
class DataLoader:
    """OKX crypto OHLCV + funding rate + open interest loader."""

    name = "okx"
    markets = {"crypto"}
    requires_auth = False

    def is_available(self) -> bool:
        """Probe public candles with a short timeout (honours proxy env)."""
        try:
            session = _okx_session()
            resp = session.get(
                CANDLES_PATH,
                params={"instId": "BTC-USDT", "bar": "1D", "limit": "1"},
                timeout=_OKX_PROBE_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning("OKX probe HTTP %s", resp.status_code)
                return False
            data = resp.json()
            return data.get("code") == "0" and bool(data.get("data"))
        except Exception as exc:  # noqa: BLE001 — availability probe
            logger.warning("OKX probe failed: %s", exc)
            return False

    def __init__(self) -> None:
        """No credentials required for public candles."""
        pass

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch crypto OHLCV via OKX public API.

        Args:
            codes: Symbols like ``["BTC-USDT", "ETH-USDT"]``.
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            fields: Ignored (OKX has no extra fields).
            interval: Bar size (1m/5m/15m/30m/1h/1H/4h/4H/1d/1D), default ``1D``.

        Returns:
            Mapping symbol -> DataFrame.
        """
        validate_date_range(start_date, end_date)

        if fields:
            logger.warning("OKX ignores extra fields: %s", fields)

        # Case aliases: connector-style ``1h``/``4h`` must not fall through to daily.
        mapped = _INTERVAL_MAP.get(interval.strip())
        if mapped is None:
            logger.warning(
                "unsupported OKX interval %r; rejecting (supported: %s)",
                interval,
                sorted(set(_INTERVAL_MAP.values())),
            )
            return {}
        interval = mapped

        codes = [c.replace("/", "-").upper() for c in codes]

        start_ts = int(pd.Timestamp(start_date).timestamp() * 1000)
        end_ts = int((pd.Timestamp(end_date) + pd.Timedelta(days=1)).timestamp() * 1000)

        # More pages for minute bars; history endpoint still needs walk-back.
        if interval in ("1m", "5m"):
            max_pages = 200
        elif interval in ("15m", "30m"):
            max_pages = 80
        else:
            max_pages = 40

        use_history = self._should_use_history(start_date)
        session = _okx_session()

        result: Dict[str, pd.DataFrame] = {}
        for symbol in codes:
            try:
                df = cached_loader_fetch(
                    source=self.name,
                    symbol=symbol,
                    timeframe=interval,
                    start_date=start_date,
                    end_date=end_date,
                    fields=None,
                    fetch=lambda symbol=symbol, use_history=use_history: self._fetch_candles(
                        session,
                        symbol,
                        start_ts,
                        end_ts,
                        interval,
                        max_pages,
                        prefer_history=use_history,
                    ),
                )
                if df is not None and not df.empty:
                    result[symbol] = df
            except Exception as exc:
                logger.warning("failed to fetch %s: %s", symbol, exc)
        return result

    @staticmethod
    def _should_use_history(start_date: str) -> bool:
        """True when the range starts older than recent-only window."""
        try:
            start = pd.Timestamp(start_date)
            age_days = (pd.Timestamp.utcnow().tz_localize(None) - start).days
            return age_days > _RECENT_ONLY_DAYS
        except Exception:
            return True

    def _fetch_candles(
        self,
        session: requests.Session,
        inst_id: str,
        start_ts: int,
        end_ts: int,
        bar: str = "1D",
        max_pages: int = 20,
        *,
        prefer_history: bool = True,
    ) -> Optional[pd.DataFrame]:
        """Paginated candle download (history endpoint for deep ranges)."""
        endpoints: list[str] = []
        if prefer_history:
            endpoints = [HISTORY_CANDLES_PATH, CANDLES_PATH]
        else:
            endpoints = [CANDLES_PATH, HISTORY_CANDLES_PATH]

        last_error: Exception | None = None
        for endpoint in endpoints:
            try:
                df = self._paginate(
                    session,
                    endpoint,
                    inst_id,
                    start_ts,
                    end_ts,
                    bar,
                    max_pages,
                )
                if df is not None and not df.empty:
                    return df
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "OKX %s failed for %s: %s — trying next endpoint",
                    endpoint.rsplit("/", 1)[-1],
                    inst_id,
                    exc,
                )

        if last_error is not None:
            logger.warning("OKX empty/failed for %s: %s", inst_id, last_error)
        else:
            logger.warning("OKX empty response: %s", inst_id)
        return None

    def _paginate(
        self,
        session: requests.Session,
        endpoint: str,
        inst_id: str,
        start_ts: int,
        end_ts: int,
        bar: str,
        max_pages: int,
    ) -> Optional[pd.DataFrame]:
        all_rows: list = []
        after = str(end_ts)
        deadline = time.monotonic() + _OKX_FETCH_BUDGET_S
        label = f"OKX fetch for {inst_id} via {endpoint.rsplit('/', 1)[-1]}"

        for _ in range(max_pages):
            check_budget(deadline, label, budget_s=_OKX_FETCH_BUDGET_S)
            params = {
                "instId": inst_id,
                "bar": bar,
                "limit": str(_MAX_PER_PAGE),
                "after": after,
            }

            def _do_request(params=params) -> dict:
                resp = session.get(
                    endpoint,
                    params=params,
                    timeout=_OKX_TIMEOUT,
                )
                # Transient gateway / rate-limit → raise for retry_with_budget
                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"OKX HTTP {resp.status_code}",
                        response=resp,
                    )
                resp.raise_for_status()
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise requests.RequestException(
                        f"OKX non-JSON response HTTP {resp.status_code}"
                    ) from exc
                code = str(data.get("code", ""))
                if code != "0":
                    # Business errors are not always transient; still surface.
                    msg = data.get("msg") or data.get("error_message") or code
                    raise requests.RequestException(f"OKX API code={code} msg={msg}")
                return data

            data = retry_with_budget(
                _do_request,
                transient=(requests.RequestException, TimeoutError),
                deadline=deadline,
                label=label,
            )
            raw_rows = data.get("data") or []
            if not raw_rows:
                break

            # Keep confirmed bars (confirm=="1"); also keep unconfirmed when
            # it is the only data returned so live partial days are not empty.
            confirmed = [r for r in raw_rows if len(r) > 8 and str(r[8]) == "1"]
            rows = confirmed if confirmed else list(raw_rows)
            all_rows.extend(rows)

            oldest_ts = int(raw_rows[-1][0])
            if oldest_ts <= start_ts or len(raw_rows) < _MAX_PER_PAGE:
                break
            after = str(oldest_ts)

        if not all_rows:
            return None

        columns = [
            "ts", "open", "high", "low", "close",
            "vol", "volCcy", "volCcyQuote", "confirm",
        ]
        # Rows may be shorter if API schema changes — pad safely
        normalized = []
        for r in all_rows:
            row = list(r) + [""] * (len(columns) - len(r))
            normalized.append(row[: len(columns)])

        df = pd.DataFrame(normalized, columns=columns)
        # OKX daily open is UTC+8 midnight (= 16:00 UTC). Keep absolute UTC
        # timestamps so multi-source merges stay consistent; floor to second.
        df["trade_date"] = pd.to_datetime(
            pd.to_numeric(df["ts"], errors="coerce"),
            unit="ms",
            utc=True,
        ).dt.tz_convert(None)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["vol"], errors="coerce").fillna(0)
        df = df.dropna(subset=["trade_date"]).set_index("trade_date").sort_index()
        df = df[~df.index.duplicated(keep="last")]

        start_dt = pd.Timestamp(start_ts, unit="ms")
        end_dt = pd.Timestamp(end_ts, unit="ms")
        df = df[(df.index >= start_dt) & (df.index < end_dt)]

        df = df[["open", "high", "low", "close", "volume"]].dropna(
            subset=["open", "high", "low", "close"]
        )
        return df if not df.empty else None

    # ------------------------------------------------------------------
    # Funding rate + Open Interest (Step 2 — crypto alpha data sources)
    # ------------------------------------------------------------------

    def fetch_funding_rate(
        self,
        symbols: list[str],
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]:
        """Fetch historical funding rates for perpetual swap symbols.

        Uses ``GET /api/v5/public/funding-rate-history``.

        Args:
            symbols: Spot instrument IDs (e.g. ``["BTC-USDT", "ETH-USDT"]``) —
                they are automatically converted to ``*-SWAP`` form.
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).

        Returns:
            Mapping ``symbol`` -> DataFrame with columns ``funding_rate``,
            ``realized_rate``, ``settled_at`` (DatetimeIndex).
        """
        validate_date_range(start, end)
        session = _okx_session()
        start_ts = int(pd.Timestamp(start).timestamp() * 1000)
        end_ts = int((pd.Timestamp(end) + pd.Timedelta(days=1)).timestamp() * 1000)

        result: dict[str, pd.DataFrame] = {}
        for raw_symbol in symbols:
            inst_id = _to_swap_inst_id(raw_symbol)
            try:
                all_rows: list[dict] = []
                after = str(end_ts)
                deadline = time.monotonic() + _OKX_FETCH_BUDGET_S
                label = f"OKX funding-rate for {inst_id}"

                for _ in range(40):
                    check_budget(deadline, label, budget_s=_OKX_FETCH_BUDGET_S)
                    params: dict[str, str] = {
                        "instId": inst_id,
                        "limit": str(_MAX_FR_PER_PAGE),
                        "after": after,
                    }
                    # OKX funding-rate-history only retains ~95 days.  When
                    # `after` points further back than that the API returns an
                    # empty list and the loop exits immediately, which leaves
                    # `all_rows` empty even though recent data exists.  Drop
                    # the `after` param on the first page when the loader's
                    # history-unsafe flag is set so we at least pick up
                    # whatever the API still holds.
                    if not all_rows and _FR_HISTORY_UNSAFE:
                        params.pop("after", None)

                    def _do_fr(params=params) -> dict:
                        resp = session.get(
                            FUNDING_RATE_PATH,
                            params=params,
                            timeout=_OKX_TIMEOUT,
                        )
                        if resp.status_code in {429, 500, 502, 503, 504}:
                            raise requests.HTTPError(
                                f"OKX HTTP {resp.status_code}",
                                response=resp,
                            )
                        resp.raise_for_status()
                        try:
                            data = resp.json()
                        except ValueError as exc:
                            raise requests.RequestException(
                                f"OKX non-JSON response HTTP {resp.status_code}"
                            ) from exc
                        code = str(data.get("code", ""))
                        if code != "0":
                            msg = data.get("msg") or data.get("error_message") or code
                            raise requests.RequestException(f"OKX API code={code} msg={msg}")
                        return data

                    data = retry_with_budget(
                        _do_fr,
                        transient=(requests.RequestException, TimeoutError),
                        deadline=deadline,
                        label=label,
                    )
                    raw_rows = data.get("data") or []
                    if not raw_rows:
                        break

                    all_rows.extend(raw_rows)
                    oldest_ts = int(float(raw_rows[-1]["fundingTime"]))
                    if oldest_ts <= start_ts or len(raw_rows) < _MAX_FR_PER_PAGE:
                        break
                    after = str(oldest_ts)

                if not all_rows:
                    continue

                df = pd.DataFrame(all_rows)
                df = df.rename(
                    columns={
                        "fundingRate": "funding_rate",
                        "realizedRate": "realized_rate",
                        "fundingTime": "settled_at_raw",
                    }
                )
                df["settled_at"] = pd.to_datetime(
                    pd.to_numeric(df["settled_at_raw"], errors="coerce"),
                    unit="ms",
                    utc=True,
                ).dt.tz_convert(None)
                df = df.dropna(subset=["settled_at"]).set_index("settled_at").sort_index()
                df = df[~df.index.duplicated(keep="last")]

                for col in ("funding_rate", "realized_rate"):
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                start_dt = pd.Timestamp(start_ts, unit="ms")
                end_dt = pd.Timestamp(end_ts, unit="ms")
                df = df[(df.index >= start_dt) & (df.index < end_dt)]

                keep_cols = [c for c in ("funding_rate", "realized_rate") if c in df.columns]
                if keep_cols and not df[keep_cols].dropna(how="all").empty:
                    result[raw_symbol] = df[keep_cols]
            except Exception as exc:
                logger.warning("failed to fetch funding rate for %s: %s", raw_symbol, exc)
        return result

    def fetch_open_interest(
        self,
        symbols: list[str],
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]:
        """Fetch current open interest snapshot for perpetual swap symbols.

        Uses ``GET /api/v5/public/open-interest``. Returns a single-row
        DataFrame per symbol (current OI), stored at the ``end`` date so it
        aligns with panel datetime indices.

        Args:
            symbols: Spot instrument IDs (e.g. ``["BTC-USDT"]``).
            start: Start date (YYYY-MM-DD) — unused for current OI snapshot.
            end: End date (YYYY-MM-DD) — used as the observation date.

        Returns:
            Mapping ``symbol`` -> DataFrame with columns ``oi``, ``oi_timestamp``.
        """
        session = _okx_session()
        end_dt = pd.Timestamp(end)

        result: dict[str, pd.DataFrame] = {}
        for raw_symbol in symbols:
            inst_id = _to_swap_inst_id(raw_symbol)
            try:
                deadline = time.monotonic() + _OKX_FETCH_BUDGET_S
                label = f"OKX OI for {inst_id}"

                def _do_oi() -> dict:
                    resp = session.get(
                        OPEN_INTEREST_PATH,
                        params={"instId": inst_id},
                        timeout=_OKX_TIMEOUT,
                    )
                    if resp.status_code in {429, 500, 502, 503, 504}:
                        raise requests.HTTPError(
                            f"OKX HTTP {resp.status_code}",
                            response=resp,
                        )
                    resp.raise_for_status()
                    try:
                        data = resp.json()
                    except ValueError as exc:
                        raise requests.RequestException(
                            f"OKX non-JSON response HTTP {resp.status_code}"
                        ) from exc
                    code = str(data.get("code", ""))
                    if code != "0":
                        msg = data.get("msg") or data.get("error_message") or code
                        raise requests.RequestException(f"OKX API code={code} msg={msg}")
                    return data

                data = retry_with_budget(
                    _do_oi,
                    transient=(requests.RequestException, TimeoutError),
                    deadline=deadline,
                    label=label,
                )
                raw_rows = data.get("data") or []
                if not raw_rows:
                    continue

                row = raw_rows[0]
                oi_val = float(row.get("oi", 0))
                oi_ts = int(float(row.get("oiTimestamp", 0)))

                df = pd.DataFrame(
                    {
                        "oi": [oi_val],
                        "oi_timestamp": [
                            pd.Timestamp(oi_ts, unit="ms", tz="UTC").tz_convert(None)
                        ],
                    },
                    index=[end_dt],
                )
                df.index.name = "trade_date"
                result[raw_symbol] = df
            except Exception as exc:
                logger.warning("failed to fetch OI for %s: %s", raw_symbol, exc)
        return result

    # ------------------------------------------------------------------
    # Open Interest History (CCXT) with local cache
    # ------------------------------------------------------------------

    _OI_CACHE_ROOT = Path.home() / ".vibe-trading" / "cache" / "oi"
    _OI_CACHE_TTL_S = 3600  # 1 hour — OI data is daily, so 1h is conservative

    @classmethod
    def _oi_cache_path(cls, symbol: str) -> Path:
        """Return the parquet cache path for *symbol*."""
        sanitised = symbol.replace("/", "-").replace(":", "-").upper()
        return cls._OI_CACHE_ROOT / f"{sanitised}.parquet"

    @classmethod
    def _oi_cache_get(cls, symbol: str) -> pd.DataFrame | None:
        """Read OI history from cache if fresh; return ``None`` on miss/stale."""
        path = cls._oi_cache_path(symbol)
        if not path.is_file():
            return None
        try:
            age = time.time() - path.stat().st_mtime
            if age > cls._OI_CACHE_TTL_S:
                return None
            return pd.read_parquet(path)
        except Exception as exc:
            logger.debug("OI cache read failed for %s: %s", symbol, exc)
            return None

    @classmethod
    def _oi_cache_put(cls, symbol: str, frame: pd.DataFrame) -> None:
        """Write OI history *frame* to cache. Best-effort — swallows errors."""
        if frame is None or frame.empty:
            return
        try:
            path = cls._oi_cache_path(symbol)
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=True)
        except Exception as exc:
            logger.debug("OI cache write failed for %s: %s", symbol, exc)

    def fetch_open_interest_history(
        self,
        symbols: list[str],
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]:
        """Fetch historical OI via CCXT ``fetch_open_interest_history()``.

        Returns a mapping ``symbol`` → DataFrame with columns ``oi``,
        ``oi_timestamp`` (same shape as :meth:`fetch_open_interest` but with
        multiple historical rows instead of a single snapshot).

        Falls back gracefully: if CCXT is unavailable, the exchange is
        unreachable, or any per-symbol fetch fails, an empty dict or partial
        results are returned (the caller is expected to fall back to the
        snapshot method).

        Args:
            symbols: Internal spot IDs (e.g. ``["BTC-USDT"]``), converted to
                CCXT swap format internally.
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).

        Returns:
            Mapping ``symbol`` → DataFrame with columns ``oi``, ``oi_timestamp``.
        """
        import ccxt

        start_dt = pd.Timestamp(start)
        end_dt = pd.Timestamp(end) + pd.Timedelta(days=1)
        since_ms = int(start_dt.timestamp() * 1000)
        # CCXT limit: how many daily bars to request; use a generous 90 to
        # cover up to ~90 calendar days (CCXT caps per-exchange silently).
        limit = (end_dt - start_dt).days + 5

        result: dict[str, pd.DataFrame] = {}

        # Create CCXT exchange once and pre-load markets so the parallel
        # /public/instruments calls in ccxt.okx.fetch_markets() don't trigger
        # OKX rate-limit connection resets (LAO-45).
        exchange: object | None = None
        try:
            import ccxt as _ccxt
            import requests as _requests

            exchange = _ccxt.okx({"enableRateLimit": True, "timeout": 30000})
            # CCXT's default requests.Session causes ConnectionResetError on
            # macOS in certain network environments.  Replace with a fresh
            # session that has been verified to work.
            exchange.session = _requests.Session()  # type: ignore[union-attr]
            exchange.load_markets()  # type: ignore[union-attr]
        except ImportError:
            logger.debug("CCXT not available for OI history; will fall back to snapshot")
        except Exception as exc:
            logger.warning("CCXT market pre-load failed: %s; will fall back to snapshot", exc)
            exchange = None

        for raw_symbol in symbols:
            ccxt_symbol = _to_ccxt_swap_symbol(raw_symbol)

            # --- Try cache first ---
            cached = self._oi_cache_get(raw_symbol)
            if cached is not None and not cached.empty:
                result[raw_symbol] = cached
                continue

            try:
                if exchange is None:
                    # Exchange pre-load failed earlier — skip this symbol.
                    continue
                ohlcv = exchange.fetch_open_interest_history(  # type: ignore[union-attr]
                    ccxt_symbol,
                    timeframe="1d",
                    since=since_ms,
                    limit=limit,
                )
                if not ohlcv:
                    continue

                rows: list[dict[str, object]] = []
                for entry in ohlcv:
                    ts = entry.get("timestamp") or entry.get("datetime")
                    oi_amount = (
                        entry.get("openInterestAmount")
                        or entry.get("openInterestValue")
                    )
                    if ts is None or oi_amount is None:
                        continue
                    rows.append(
                        {
                            "oi": float(oi_amount),
                            "oi_timestamp": pd.Timestamp(
                                int(float(str(ts))), unit="ms", tz="UTC"
                            ).tz_convert(None),
                        }
                    )

                if not rows:
                    continue

                df = pd.DataFrame(rows)
                df = df.set_index("oi_timestamp").sort_index()
                df = df[~df.index.duplicated(keep="last")]
                df = df[(df.index >= start_dt) & (df.index < end_dt)]
                df.index.name = "trade_date"

                if df.empty:
                    continue

                self._oi_cache_put(raw_symbol, df)
                result[raw_symbol] = df
            except ImportError:
                logger.debug("CCXT not available for OI history; will fall back to snapshot")
                break
            except Exception as exc:
                logger.warning(
                    "failed to fetch OI history for %s via CCXT: %s",
                    raw_symbol, exc,
                )

        return result
