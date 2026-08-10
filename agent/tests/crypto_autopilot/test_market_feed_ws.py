"""Tests for the OKX WebSocket live-candle stream (v2).

All WebSocket I/O is mocked — no real network connections are made.
Covers channel mapping, candle array conversion, confirmed-bar filtering,
reconnect-on-drop, and subscription-error termination.
"""

from __future__ import annotations

import asyncio
import json

import pandas as pd
import pytest

from src.crypto_autopilot.config import AutopilotConfig
from src.crypto_autopilot.market_feed import MarketFeed

__all__ = []


# ---------------------------------------------------------------------------
# WebSocket fakes
# ---------------------------------------------------------------------------


def _candle(ts_ms: int, close: str = "100.0", confirm: str = "1") -> list[str]:
    """Build an OKX WS candle array (9 fields, newest-first per payload)."""
    return [
        str(ts_ms), "99.0", "101.0", "98.0", close,
        "12.5", "12.5", "1250.0", confirm,
    ]


def _candle_msg(ts_ms: int, close: str = "100.0", confirm: str = "1") -> str:
    """Wrap a candle array in an OKX WS data message."""
    return json.dumps(
        {
            "arg": {"channel": "candle1m", "instId": "BTC-USDT"},
            "data": [_candle(ts_ms, close, confirm)],
        }
    )


def _error_msg() -> str:
    return json.dumps({"event": "error", "code": "60011", "msg": "bad instrument"})


class _MsgIter:
    def __init__(self, messages: list[str], drop: Exception | None = None) -> None:
        self._it = iter(messages)
        self._drop = drop

    async def __anext__(self) -> str:
        try:
            return next(self._it)
        except StopIteration:
            if self._drop is not None:
                raise self._drop from None
            raise StopAsyncIteration


class _FakeWS:
    def __init__(self, messages: list[str], drop: Exception | None = None) -> None:
        self._messages = messages
        self._drop = drop
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)

    def __aiter__(self) -> _MsgIter:
        return _MsgIter(self._messages, self._drop)


class _FakeConn:
    def __init__(self, ws: _FakeWS) -> None:
        self._ws = ws

    async def __aenter__(self) -> _FakeWS:
        return self._ws

    async def __aexit__(self, *exc: object) -> bool:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _feed() -> MarketFeed:
    return MarketFeed(
        okx_config=None,
        autopilot_config=AutopilotConfig(pairs=["BTC-USDT"]),
    )


async def _collect(agen) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    async for df in agen:
        frames.append(df)
    return frames


def _run(agen) -> list[pd.DataFrame]:
    return asyncio.run(_collect(agen))


# ---------------------------------------------------------------------------
# Channel mapping
# ---------------------------------------------------------------------------


class TestChannelMapping:
    def test_unsupported_period_raises_value_error(self) -> None:
        """A bar size with no OKX candle channel fails fast, before any I/O."""
        with pytest.raises(ValueError, match="unsupported bar size"):
            _run(_feed().stream_bars("BTC-USDT", "2s"))

    def test_supported_period_subscribes_correct_channel(self, monkeypatch) -> None:
        """The subscribe payload maps 1m -> candle1m on the public endpoint."""
        captured: dict = {}

        def fake_connect(uri):
            # ``websockets.connect`` is synchronous and returns an async
            # context manager — a plain (non-async) mock is required.
            captured["uri"] = uri
            return _FakeConn(_FakeWS([_error_msg()]))

        monkeypatch.setattr("websockets.connect", fake_connect)
        _run(_feed().stream_bars("BTC-USDT", "1m"))
        # Default feed profile is paper — the demo endpoint carries a brokerId query.
        assert "/ws/v5/public" in captured["uri"]


# ---------------------------------------------------------------------------
# Bar conversion + filtering
# ---------------------------------------------------------------------------


class TestStreamBars:
    def test_yields_confirmed_bar(self, monkeypatch) -> None:
        """A settled candle yields a one-row OHLCV DataFrame."""
        monkeypatch.setattr(
            "websockets.connect",
            lambda uri: _FakeConn(_FakeWS([_candle_msg(1_700_000_000_000), _error_msg()])),
        )
        frames = _run(_feed().stream_bars("BTC-USDT", "1m"))
        assert len(frames) == 1
        df = frames[0]
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert df["close"].iloc[0] == 100.0
        assert df.index[0] == pd.Timestamp("2023-11-14 22:13:20")

    def test_filters_unconfirmed_bars(self, monkeypatch) -> None:
        """In-progress candles (confirm=0) are skipped; settled ones pass."""
        msgs = [
            _candle_msg(1_700_000_000_000, close="99.0", confirm="0"),
            _candle_msg(1_700_000_060_000, close="100.0", confirm="1"),
            _error_msg(),
        ]
        monkeypatch.setattr(
            "websockets.connect",
            lambda uri: _FakeConn(_FakeWS(msgs)),
        )
        frames = _run(_feed().stream_bars("BTC-USDT", "1m"))
        assert len(frames) == 1
        assert frames[0]["close"].iloc[0] == 100.0

    def test_ignores_non_data_messages(self, monkeypatch) -> None:
        """Subscription-ack and ping messages yield nothing."""
        msgs = [
            json.dumps({"event": "subscribe", "arg": {"channel": "candle1m"}}),
            _candle_msg(1_700_000_000_000),
            _error_msg(),
        ]
        monkeypatch.setattr(
            "websockets.connect",
            lambda uri: _FakeConn(_FakeWS(msgs)),
        )
        frames = _run(_feed().stream_bars("BTC-USDT", "1m"))
        assert len(frames) == 1


# ---------------------------------------------------------------------------
# Lifecycle: reconnect + fatal errors
# ---------------------------------------------------------------------------


class TestStreamLifecycle:
    def test_subscription_error_ends_stream(self, monkeypatch) -> None:
        """An OKX error event (e.g. unknown instrument) terminates the stream."""
        monkeypatch.setattr(
            "websockets.connect",
            lambda uri: _FakeConn(_FakeWS([_error_msg()])),
        )
        assert _run(_feed().stream_bars("BTC-USDT", "1m")) == []

    def test_reconnects_after_transport_drop(self, monkeypatch) -> None:
        """A failed connect is retried; bars flow after the retry."""
        attempts = {"n": 0}

        def fake_connect(uri):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError("boom")
            return _FakeConn(
                _FakeWS([_candle_msg(1_700_000_060_000), _error_msg()])
            )

        monkeypatch.setattr("websockets.connect", fake_connect)
        frames = _run(_feed().stream_bars("BTC-USDT", "1m"))
        assert attempts["n"] == 2
        assert len(frames) == 1
        assert frames[0]["close"].iloc[0] == 100.0

    def test_reconnect_backoff_grows(self, monkeypatch) -> None:
        """Backoff doubles between failed connect attempts (1s -> 2s -> 4s)."""
        sleeps: list[float] = []
        attempts = {"n": 0}

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        def fake_connect(uri):
            attempts["n"] += 1
            if attempts["n"] <= 3:
                raise ConnectionError("down")
            return _FakeConn(_FakeWS([_error_msg()]))

        monkeypatch.setattr("websockets.connect", fake_connect)
        monkeypatch.setattr("asyncio.sleep", fake_sleep)
        _run(_feed().stream_bars("BTC-USDT", "1m"))
        assert sleeps == [1.0, 2.0, 4.0]
