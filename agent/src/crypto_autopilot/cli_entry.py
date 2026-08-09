"""CLI entry points for the crypto autopilot — start, stop, status.

Provides ``register_parser`` to integrate with an argparse-based CLI
(subcommand pattern), plus standalone command handlers that can be
invoked directly from the slash-command router or tests.

Usage via argparse::

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register_parser(subparsers)
    args = parser.parse_args(["autopilot", "start"])

Usage via direct calls::

    from src.crypto_autopilot.cli_entry import start_command, stop_command, status_command
    exit_code = start_command(None)

The ``stop`` command trips the filesystem kill switch
(:func:`src.live.halt.trip_halt`) to signal the running autopilot to
halt — it does not send a signal to the process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["register_parser", "dispatch", "start_command", "stop_command", "status_command"]

#: Broker key used by the autopilot's kill switch.
_BROKER_KEY = "okx"


# ------------------------------------------------------------------
# Argparse registration
# ------------------------------------------------------------------


def register_parser(subparsers: Any) -> None:
    """Register the ``autopilot`` subcommand with start/stop/status sub-subcommands.

    Follows the argparse subcommand pattern: creates an ``autopilot``
    sub-parser, then adds ``start``, ``stop``, and ``status`` sub-parsers
    beneath it.

    Args:
        subparsers: The argparse subparsers action from the parent parser
            (``parser.add_subparsers()``).
    """
    autopilot_parser = subparsers.add_parser(
        "autopilot",
        help="Crypto autopilot — 24/7 autonomous trading loop",
        description="Manage the crypto autopilot pipeline: start, stop, status.",
    )
    autopilot_sub = autopilot_parser.add_subparsers(
        dest="autopilot_action",
        help="Autopilot action",
    )

    # start
    start_parser = autopilot_sub.add_parser(
        "start",
        help="Start the autopilot loop",
        description="Start the 24/7 autopilot pipeline (collect → mine → evaluate → trade → feedback).",
    )
    start_parser.set_defaults(func=start_command)

    # stop
    stop_parser = autopilot_sub.add_parser(
        "stop",
        help="Stop the autopilot by tripping the kill switch",
        description="Trip the filesystem HALT sentinel to signal the running autopilot to stop.",
    )
    stop_parser.set_defaults(func=stop_command)

    # status
    status_parser = autopilot_sub.add_parser(
        "status",
        help="Show autopilot pipeline status",
        description="Load the HealthMonitor pipeline state and print a status summary.",
    )
    status_parser.set_defaults(func=status_command)


# ------------------------------------------------------------------
# Command handlers
# ------------------------------------------------------------------


def start_command(args: Any = None) -> int:
    """Create AutopilotOrchestrator and run its asyncio loop.

    Handles KeyboardInterrupt gracefully. The loop runs until
    :meth:`stop` is called on the orchestrator or the process
    receives SIGINT/SIGTERM.

    Args:
        args: Parsed argparse namespace (unused — config comes from env).

    Returns:
        Exit code: 0 on clean exit, 1 on error.
    """
    from src.crypto_autopilot.config import load_autopilot_config
    from src.crypto_autopilot.orchestrator import AutopilotOrchestrator

    config = load_autopilot_config()
    if not config.enabled:
        print("autopilot is not enabled (set AUTOPILOT_ENABLED=1)")
        return 1

    print(f"starting autopilot (pairs={config.pairs})")
    print(f"  mine every {config.mine_interval_hours}h")
    print(f"  evaluate every {config.evaluate_interval_hours}h")
    print(f"  trade every {config.trade_interval_minutes}m")
    print(f"  feedback every {config.feedback_interval_hours}h")

    orchestrator = AutopilotOrchestrator(config=config)

    try:
        asyncio.run(_run_with_signal_handler(orchestrator))
    except KeyboardInterrupt:
        print("\ninterrupted — saving state")
        try:
            asyncio.run(orchestrator.stop())
        except Exception as exc:  # noqa: BLE001
            logger.warning("error during shutdown: %s", exc)
    except Exception as exc:
        logger.error("autopilot crashed: %s", exc, exc_info=True)
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("autopilot stopped")
    return 0


def stop_command(args: Any = None) -> int:
    """Trip the HALT file to signal the running autopilot to stop.

    Uses :func:`src.live.halt.trip_halt` with ``broker="okx"`` to write
    the kill-switch sentinel. The running autopilot loop observes this
    via :meth:`RiskMonitor.is_halted` and stops trading.

    Args:
        args: Parsed argparse namespace (unused).

    Returns:
        Exit code: 0 on success, 1 on error.
    """
    try:
        from src.live.halt import trip_halt

        path = trip_halt(
            by="cli",
            reason="manual stop via autopilot CLI",
            broker=_BROKER_KEY,
        )
        print(f"halt tripped for {_BROKER_KEY}: {path}")
        print("the running autopilot will observe the HALT sentinel and stop trading")
        return 0
    except Exception as exc:
        logger.error("failed to trip halt: %s", exc, exc_info=True)
        print(f"error: failed to trip halt — {exc}", file=sys.stderr)
        return 1


def status_command(args: Any = None) -> int:
    """Load HealthMonitor, read pipeline state, print status summary.

    Reads the persisted pipeline state and heartbeat from the
    :class:`HealthMonitor` and prints a human-readable summary.

    Args:
        args: Parsed argparse namespace (unused).

    Returns:
        Exit code: 0 on success, 1 on error.
    """
    from pathlib import Path

    from src.crypto_autopilot.health import HealthMonitor

    runtime_root = (
        Path(__file__).resolve().parents[2] / "runs" / "autopilot"
    )
    health = HealthMonitor(runtime_root)

    # Pipeline state.
    state = health.load_pipeline_state()
    if state is None:
        print("no pipeline state found (autopilot may not have run yet)")
    else:
        print(f"pipeline phase: {state.phase.value}")
        print(f"tick count:     {state.tick_count}")
        print(f"last tick at:   {state.last_tick_at or 'never'}")
        print(f"updated at:     {state.updated_at or 'never'}")
        if state.active_factor_id:
            print(f"active factor:  {state.active_factor_id}")

    # Heartbeat / liveness.
    is_alive = health.is_alive()
    is_stale = health.is_stale()
    print(f"\nhealth:")
    print(f"  alive:  {is_alive}")
    print(f"  stale:  {is_stale}")

    # Kill switch status.
    try:
        from src.live.halt import halt_flag_set

        halted = halt_flag_set(_BROKER_KEY)
        print(f"  halted: {halted}")
    except Exception as exc:
        print(f"  halted: unknown ({exc})")

    return 0


# ------------------------------------------------------------------
# Dispatch
# ------------------------------------------------------------------


_DISPATCH: dict[str, Any] = {
    "start": start_command,
    "stop": stop_command,
    "status": status_command,
}


def dispatch(args: Any) -> int:
    """Dispatch ``autopilot <sub>`` to its handler.

    Follows the ``add_subparser`` + ``dispatch`` pattern used by the other
    CLI subcommand modules (alpha, hypothesis, playbook).

    Args:
        args: Parsed argparse namespace with ``autopilot_action`` set.

    Returns:
        A process exit code: ``0`` on success, ``1`` on a failed operation,
        ``2`` on a usage error.
    """
    action = getattr(args, "autopilot_action", None)
    handler = _DISPATCH.get(action or "")
    if handler is None:
        print(
            "autopilot requires a subcommand. "
            "Try: vibe-trading autopilot status"
        )
        return 2
    return int(handler(args))


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


async def _run_with_signal_handler(orchestrator: Any) -> None:
    """Run the orchestrator with SIGINT/SIGTERM handling for graceful shutdown.

    Registers signal handlers that call :meth:`orchestrator.stop` on
    SIGINT/SIGTERM, then starts the orchestrator loop.

    Args:
        orchestrator: The :class:`AutopilotOrchestrator` instance.
    """
    import signal

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("signal received — stopping orchestrator")
        stop_event.set()

    # Register signal handlers (Unix only; Windows uses KeyboardInterrupt).
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler.
            pass

    # Start the orchestrator as a task.
    task = loop.create_task(orchestrator.start())

    # Wait for either the task to complete or a signal.
    done, pending = await asyncio.wait(
        [task, asyncio.create_task(stop_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If we got a signal, stop the orchestrator.
    if stop_event.is_set() and not task.done():
        await orchestrator.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
