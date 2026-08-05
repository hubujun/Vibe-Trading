"""Baidu Finance loader: free, no-auth A-share daily OHLCV via PAE API.

Uses Baidu Finance's selfselect/getstockquotation endpoint which serves
daily K-line data without authentication or IP throttling.

Covers: A-shares (SH/SZ).  No API token required.

Note: Baidu's API performs TLS fingerprinting that blocks Python's urllib /
requests. This loader shells out to ``curl`` (always present on macOS / Linux)
to bypass the fingerprint check. curl_cffi is also supported as a faster
alternative when available.

API format:
  GET https://finance.pae.baidu.com/selfselect/getstockquotation
    ?all=1&isIndex=false&isStock=true&newFormat=1
    &group=quotation_kline_ab&finClientType=pc
    &code={6位代码}&start_time=&ktype=1

Response:
  {"ResultCode":"0","Result":{"newMarketData":{"marketData":"<semicolon-separated CSV>"}}}
  
Each CSV row has 18 columns: timestamp, time, open, close, volume, high, low,
amount, range, ratio, turnoverratio, preClose, ma5avgprice, ma5volume,
ma10avgprice, ma10volume, ma20avgprice, ma20volume.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Dict, List, Optional

import pandas as pd

from backtest.loaders.base import cached_loader_fetch, validate_date_range
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)

_BASE_URL = "https://finance.pae.baidu.com/selfselect/getstockquotation"

# Try curl_cffi first (faster, in-process), fall back to subprocess curl.
_CURL_CFFI_AVAILABLE = False
try:
    from curl_cffi import requests as _curl_requests  # noqa: F401
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    pass


def _is_a_share(code: str) -> bool:
    return code.upper().endswith((".SZ", ".SH"))


def _to_baidu_code(code: str) -> str:
    """Convert Vibe-Trading symbol (e.g. 600519.SH) to Baidu 6-digit code."""
    parts = code.upper().split(".")
    return parts[0]


def _http_get(url: str, timeout: int = 15) -> str:
    """Fetch URL body as text, using curl_cffi when available, otherwise subprocess curl.

    Both approaches use the system TLS stack (not OpenSSL), bypassing Baidu's
    TLS fingerprinting that blocks Python's urllib / requests.
    """
    if _CURL_CFFI_AVAILABLE:
        from curl_cffi import requests as curl_requests
        resp = curl_requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://finance.baidu.com/",
            },
            impersonate="chrome110",
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.text

    # Fallback: subprocess curl.
    result = subprocess.run(
        [
            "curl", "-sS", "--max-time", str(timeout),
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "-H", "Referer: https://finance.baidu.com/",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )
    if result.returncode != 0:
        raise OSError(f"curl exited with {result.returncode}: {result.stderr.strip()}")
    return result.stdout


@register
class DataLoader:
    """Baidu Finance A-share daily OHLCV loader (free, HTTP, no auth)."""

    name = "baidu"
    markets = {"a_share"}
    requires_auth = False

    def is_available(self) -> bool:
        """Always available — uses plain HTTP."""
        return True

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        validate_date_range(start_date, end_date)
        del fields

        if interval.strip().lower() not in {"1d", "d", "day", "daily"}:
            logger.warning(
                "baidu supports daily bars only; rejecting interval=%s",
                interval,
            )
            return {}

        result: Dict[str, pd.DataFrame] = {}
        for code in codes:
            try:
                df = cached_loader_fetch(
                    source=self.name,
                    symbol=code,
                    timeframe=interval,
                    start_date=start_date,
                    end_date=end_date,
                    fields=None,
                    fetch=lambda code=code: self._fetch_one(code, start_date, end_date),
                )
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as exc:
                logger.warning("baidu failed for %s: %s", code, exc)
        return result

    def _fetch_one(
        self, code: str, start_date: str, end_date: str,
    ) -> Optional[pd.DataFrame]:
        if not _is_a_share(code):
            return None

        baidu_code = _to_baidu_code(code)

        url = (
            f"{_BASE_URL}?all=1&isIndex=false&isStock=true&newFormat=1"
            f"&group=quotation_kline_ab&finClientType=pc"
            f"&code={baidu_code}&start_time=&ktype=1"
        )

        try:
            raw = _http_get(url)
        except Exception as exc:
            logger.warning("baidu HTTP request failed for %s: %s", code, exc)
            return None

        data = json.loads(raw)
        if data.get("ResultCode") != "0":
            logger.warning("baidu API error for %s: ResultCode=%s", code, data.get("ResultCode"))
            return None

        market_data = data.get("Result", {}).get("newMarketData", {})
        csv_str = market_data.get("marketData", "")
        if not csv_str:
            return None

        # Parse semicolon-separated CSV rows
        rows = []
        for line in csv_str.split(";"):
            line = line.strip()
            if not line:
                continue
            fields_list = line.split(",")
            if len(fields_list) < 18:
                continue
            try:
                rows.append({
                    "trade_date": fields_list[1],      # time (YYYY-MM-DD)
                    "open": float(fields_list[2]),
                    "close": float(fields_list[3]),
                    "volume": float(fields_list[4]),
                    "high": float(fields_list[5]),
                    "low": float(fields_list[6]),
                })
            except (ValueError, IndexError):
                continue

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date").sort_index()
        df = df[["open", "high", "low", "close", "volume"]].dropna(
            subset=["open", "high", "low", "close"]
        )

        # Clip to requested date range
        if not df.empty:
            df = df.loc[pd.Timestamp(start_date):pd.Timestamp(end_date)]

        return df if not df.empty else None
