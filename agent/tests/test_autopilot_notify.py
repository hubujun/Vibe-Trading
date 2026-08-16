"""Tests for the autopilot notify worker (outbox → IM channel bus).

Covers target resolution from the operator channel config, message
rendering, delivery to the channel bus, and the ``.sent`` marker
semantics (delivered / unreadable / withheld when no targets exist yet).
The autopilot-side outbox writer is tested in
``tests/crypto_autopilot/test_notifier.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.api import autopilot_notify

__all__ = []


def _write_outbox(worker, kind="order_filled", **overrides) -> Path:
    """Write one pending outbox file under the worker's outbox dir."""
    payload = {
        "id": "n1",
        "kind": kind,
        "title": "Order filled",
        "body": "notional=10 USDT",
        "created_at": "2026-08-09T00:00:00+00:00",
        "meta": {"symbol": "BTC-USDT", "side": "buy"},
    }
    payload.update(overrides)
    path = worker._outbox / "20260809T000000_n1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _FakeBus:
    def __init__(self) -> None:
        self.messages = []

    async def publish_outbound(self, message) -> None:
        self.messages.append(message)


class _FakeRuntime:
    def __init__(self) -> None:
        self.bus = _FakeBus()


@pytest.fixture
def worker(tmp_path, monkeypatch) -> autopilot_notify.AutopilotNotifyWorker:
    """A worker whose outbox resolves under a temp dir."""
    w = autopilot_notify.AutopilotNotifyWorker(poll_interval_s=0.5)
    monkeypatch.setattr(w, "_outbox", tmp_path / "notifications")
    return w


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRender:
    def test_render_headline_body_meta(self) -> None:
        """Known kinds use their template headline + body + meta details."""
        text = autopilot_notify._render(
            {
                "kind": "order_filled",
                "body": "notional=10 USDT",
                "meta": {"symbol": "BTC-USDT", "side": "buy"},
            }
        )
        assert "Order filled" in text
        assert "notional=10 USDT" in text
        assert "symbol: BTC-USDT" in text
        assert "side: buy" in text

    def test_render_skips_body_that_matches_fallback(self) -> None:
        """A body identical to the fallback text is not duplicated."""
        text = autopilot_notify._render(
            {"kind": "halt_tripped", "body": "Kill switch tripped"}
        )
        assert "Kill switch tripped" not in text

    def test_render_unknown_kind_uses_payload_text(self) -> None:
        """Unregistered kinds fall back to the raw title/body."""
        text = autopilot_notify._render(
            {"kind": "future_kind", "title": "Custom", "body": "detail"}
        )
        assert "Custom" in text


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


class TestResolveTargets:
    def test_prefers_notify_chat_ids(self, monkeypatch) -> None:
        """notify_chat_ids wins over operators when both are present."""
        monkeypatch.setattr(
            autopilot_notify,
            "load_channels_config",
            lambda: {
                "telegram": {
                    "enabled": True,
                    "operators": ["op1"],
                    "notify_chat_ids": ["chat-9"],
                }
            },
        )
        assert autopilot_notify._resolve_targets() == {"telegram": ["chat-9"]}

    def test_operators_used_as_fallback(self, monkeypatch) -> None:
        """Without notify_chat_ids the operators list is used."""
        monkeypatch.setattr(
            autopilot_notify,
            "load_channels_config",
            lambda: {"slack": {"operators": ["op1", "op2"]}},
        )
        assert autopilot_notify._resolve_targets() == {"slack": ["op1", "op2"]}

    def test_disabled_channels_skipped(self, monkeypatch) -> None:
        """Channels with enabled=False never receive notifications."""
        monkeypatch.setattr(
            autopilot_notify,
            "load_channels_config",
            lambda: {
                "telegram": {"enabled": False, "operators": ["op1"]},
                "slack": {"operators": ["op2"]},
            },
        )
        assert autopilot_notify._resolve_targets() == {"slack": ["op2"]}

    def test_config_error_yields_empty(self, monkeypatch) -> None:
        """A broken channels config degrades to no targets (not a crash)."""
        monkeypatch.setattr(
            autopilot_notify,
            "load_channels_config",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert autopilot_notify._resolve_targets() == {}


# ---------------------------------------------------------------------------
# Outbox processing
# ---------------------------------------------------------------------------


class TestProcessPending:
    def test_relays_file_and_marks_sent(self, worker, tmp_path, monkeypatch) -> None:
        """A pending file is published to every resolved target and renamed."""
        path = _write_outbox(worker)
        fake_runtime = _FakeRuntime()
        monkeypatch.setattr("api_server._get_channel_runtime", lambda: fake_runtime)
        monkeypatch.setattr(
            autopilot_notify,
            "load_channels_config",
            lambda: {"telegram": {"notify_chat_ids": ["op1"]}},
        )

        asyncio.run(worker._process_pending())

        assert len(fake_runtime.bus.messages) == 1
        msg = fake_runtime.bus.messages[0]
        assert msg.channel == "telegram"
        assert msg.chat_id == "op1"
        assert msg.content
        assert "Order filled" in msg.content
        assert msg.metadata["kind"] == "order_filled"
        assert msg.metadata["notification_id"] == "n1"
        assert not path.exists()
        assert (tmp_path / "notifications" / "20260809T000000_n1.json.sent").exists()

    def test_keeps_files_when_no_targets(self, worker, tmp_path, monkeypatch) -> None:
        """No resolvable targets → files stay for a later poll (backfill)."""
        path = _write_outbox(worker)
        monkeypatch.setattr(
            autopilot_notify,
            "load_channels_config",
            lambda: {},
        )

        asyncio.run(worker._process_pending())

        assert path.exists()
        assert not list((tmp_path / "notifications").glob("*.sent"))

    def test_marks_unreadable_files_sent(self, worker, tmp_path, monkeypatch) -> None:
        """A corrupt file is marked .sent to avoid a poison-queue loop."""
        path = _write_outbox(worker)
        path.write_text("{not json", encoding="utf-8")
        fake_runtime = _FakeRuntime()
        monkeypatch.setattr("api_server._get_channel_runtime", lambda: fake_runtime)
        monkeypatch.setattr(
            autopilot_notify,
            "load_channels_config",
            lambda: {"telegram": {"notify_chat_ids": ["op1"]}},
        )

        asyncio.run(worker._process_pending())

        assert len(fake_runtime.bus.messages) == 0
        assert not path.exists()
        assert (tmp_path / "notifications" / "20260809T000000_n1.json.sent").exists()

    def test_publish_failure_does_not_block_other_targets(
        self, worker, tmp_path, monkeypatch,
    ) -> None:
        """A failing target is logged; other targets still get the message."""
        _write_outbox(worker)

        class _BoomBus:
            async def publish_outbound(self, message) -> None:
                if message.chat_id == "bad":
                    raise RuntimeError("channel down")

        class _BoomRuntime:
            bus = _BoomBus()

        monkeypatch.setattr("api_server._get_channel_runtime", lambda: _BoomRuntime())
        monkeypatch.setattr(
            autopilot_notify,
            "load_channels_config",
            lambda: {"telegram": {"notify_chat_ids": ["bad", "good"]}},
        )

        asyncio.run(worker._process_pending())

        # The file is still marked sent after best-effort delivery.
        assert not list((tmp_path / "notifications").glob("*.json"))
        assert list((tmp_path / "notifications").glob("*.sent"))

    def test_missing_outbox_dir_is_noop(self, worker, tmp_path) -> None:
        """No outbox directory → processing is a safe no-op."""
        asyncio.run(worker._process_pending())  # should not raise


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_start_is_idempotent_and_stop_cancels(self, worker) -> None:
        """start() twice is safe; stop() cancels the polling task."""

        async def _run() -> None:
            worker.start()
            worker.start()
            assert worker._task is not None
            await worker.stop()
            assert worker._task is None

        asyncio.run(_run())
