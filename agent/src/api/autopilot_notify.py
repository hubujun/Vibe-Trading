"""Autopilot IM notification worker — relays the autopilot outbox to chat channels.

The autopilot process writes notification files into
``<agent>/runs/autopilot/notifications/`` (see
:mod:`src.crypto_autopilot.notifier`). This worker polls that directory
and, for every pending file, publishes an :class:`OutboundMessage` to the
existing IM channel bus (:mod:`src.channels`) so operators receive order,
halt, factor-lifecycle, and recovery events without opening a session.

Delivery targets are resolved per enabled channel from the operator config:
a channel section's ``notify_chat_ids`` list takes precedence, otherwise the
section's ``operators`` list is used (for most IM platforms the operator id
*is* the private-chat id). Files whose targets cannot be resolved are left
in place and retried on the next poll — enabling a channel later backfills
the backlog; files that are unreadable are marked ``.sent`` to avoid a
poison-queue loop.

Lifecycle is bound to the API server (``_start_autopilot_notify`` /
``_stop_autopilot_notify``), mirroring the scheduled-research executor.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from src.channels.bus.events import OutboundMessage
from src.channels.config import load_channels_config

logger = logging.getLogger(__name__)

__all__ = [
    "AutopilotNotifyWorker",
    "_autopilot_notify_worker",
    "_start_autopilot_notify",
    "_stop_autopilot_notify",
]

#: Poll interval for the notification outbox directory.
_POLL_INTERVAL_S = 5.0

#: Outbox directory under the autopilot runtime root.
_NOTIFY_DIR = "notifications"

#: Suffix applied to processed outbox files (atomic marker via rename).
_SENT_SUFFIX = ".sent"

#: Per-kind message templates; the kind key is also used in metadata so
#: rich clients can render structured cards.
_TEMPLATES: dict[str, tuple[str, str]] = {
    "order_filled": ("📈 Order filled", "Order filled"),
    "halt_tripped": ("🛑 Autopilot halted", "Kill switch tripped"),
    "factor_promoted": ("🚀 Factor promoted", "Factor promoted to live"),
    "factor_retired": ("🗑 Factor retired", "Factor retired"),
    "crash_recovered": ("♻️ Autopilot recovered", "Autopilot recovered"),
    "autopilot_down": ("📉 Autopilot offline", "Autopilot heartbeat stale"),
    "data_stale": ("📊 Market data stale", "Market data lagging"),
}


def _runtime_root() -> Path:
    """Return the autopilot runtime root (``<agent>/runs/autopilot``)."""
    return Path(__file__).resolve().parents[2] / "runs" / "autopilot"


def _render(payload: dict[str, Any]) -> str:
    """Render an outbox payload into a channel-agnostic message body."""
    kind = str(payload.get("kind", ""))
    headline, fallback = _TEMPLATES.get(kind, (str(payload.get("title") or ""), str(payload.get("body") or "")))
    lines = [headline]
    body = str(payload.get("body") or "")
    if body and body != fallback:
        lines.append(body)
    meta = payload.get("meta") or {}
    if meta:
        detail = " · ".join(f"{k}: {v}" for k, v in meta.items() if v is not None)
        if detail:
            lines.append(detail)
    return "\n".join(lines)


class AutopilotNotifyWorker:
    """Poll the autopilot outbox and relay events to the IM channel bus."""

    def __init__(self, poll_interval_s: float = _POLL_INTERVAL_S) -> None:
        """Initialize the worker (call :meth:`start` to begin polling).

        Args:
            poll_interval_s: Seconds between outbox scans.
        """
        self._poll_interval_s = max(0.5, poll_interval_s)
        self._outbox = _runtime_root() / _NOTIFY_DIR
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the polling loop (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("autopilot notify worker started (outbox=%s)", self._outbox)

    async def stop(self) -> None:
        """Stop the polling loop (idempotent)."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("autopilot notify worker stopped")

    async def _poll_loop(self) -> None:
        """Scan the outbox until cancelled."""
        while True:
            try:
                await self._process_pending()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a bad batch must not kill the loop
                logger.warning("autopilot notify poll failed: %s", exc, exc_info=True)
            await asyncio.sleep(self._poll_interval_s)

    async def _process_pending(self) -> None:
        """Relay every pending outbox file; mark each as processed."""
        if not self._outbox.is_dir():
            return
        targets = _resolve_targets()
        if not targets:
            # No channel targets yet — leave files for a later poll so the
            # backlog is delivered once a channel is enabled.
            return
        for path in sorted(self._outbox.glob("*.json")):
            payload = self._read_payload(path)
            if payload is None:
                self._mark_sent(path)
                continue
            for channel, chat_ids in targets.items():
                for chat_id in chat_ids:
                    try:
                        self._publish(channel, chat_id, payload)
                    except Exception as exc:  # noqa: BLE001 — per-target isolation
                        logger.warning(
                            "autopilot notify %s -> %s:%s failed: %s",
                            payload.get("kind"), channel, chat_id, exc,
                        )
            self._mark_sent(path)

    def _publish(
        self,
        channel: str,
        chat_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Publish one outbound message for the event (fire-and-forget)."""
        from api_server import _get_channel_runtime

        runtime = _get_channel_runtime()
        if runtime is None or not runtime.bus:
            raise RuntimeError("channel runtime not available")
        runtime.bus.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=_render(payload),
                metadata={
                    "_autopilot_notify": True,
                    "kind": payload.get("kind", ""),
                    "notification_id": payload.get("id", ""),
                    "created_at": payload.get("created_at", ""),
                },
            )
        )

    @staticmethod
    def _read_payload(path: Path) -> dict[str, Any] | None:
        """Parse an outbox file, returning ``None`` when unreadable."""
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (OSError, ValueError, TypeError):
            logger.warning("unreadable autopilot notification %s", path, exc_info=True)
            return None

    def _mark_sent(self, path: Path) -> None:
        """Atomically mark an outbox file as delivered via rename."""
        try:
            path.rename(path.with_name(path.name + _SENT_SUFFIX))
        except OSError as exc:
            logger.warning("failed to mark notification %s sent: %s", path, exc)


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def _resolve_targets() -> dict[str, list[str]]:
    """Resolve notification targets from the operator channel config.

    Returns:
        Mapping ``{channel_name: [chat_id, ...]}`` for channels that have an
        explicit ``notify_chat_ids`` list or at least one ``operators``
        entry. Channels without targets are omitted.
    """
    try:
        config = load_channels_config()
    except Exception as exc:  # noqa: BLE001 — config problems must not kill polling
        logger.warning("cannot load channels config for autopilot notify: %s", exc)
        return {}
    if not isinstance(config, dict):
        return {}
    targets: dict[str, list[str]] = {}
    for name, section in config.items():
        if not isinstance(section, dict):
            continue
        if section.get("enabled") is False:
            continue
        chat_ids = section.get("notify_chat_ids")
        if isinstance(chat_ids, list):
            cleaned = [str(c) for c in chat_ids if str(c).strip()]
            if cleaned:
                targets[str(name)] = cleaned
                continue
        operators = section.get("operators")
        if isinstance(operators, list):
            cleaned = [str(o) for o in operators if str(o).strip()]
            if cleaned:
                targets[str(name)] = cleaned
    return targets


# ---------------------------------------------------------------------------
# Lifecycle hooks (bound by api_server)
# ---------------------------------------------------------------------------

_autopilot_notify_worker: AutopilotNotifyWorker | None = None


def _start_autopilot_notify() -> None:
    """Start the singleton autopilot notify worker."""
    global _autopilot_notify_worker
    if _autopilot_notify_worker is None:
        _autopilot_notify_worker = AutopilotNotifyWorker()
    _autopilot_notify_worker.start()


async def _stop_autopilot_notify() -> None:
    """Stop the singleton autopilot notify worker if it was started."""
    worker = _autopilot_notify_worker
    if worker is not None:
        await worker.stop()
