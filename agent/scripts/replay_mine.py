"""Historical-replay factor mining for the crypto autopilot (Plan A).

Development-only script — not included in the package.
Run: cd agent && python scripts/replay_mine.py [--rounds 3] [--bars 1440]

Mines factor candidates with the SAME pipeline the autopilot runs every
``mine_interval_hours``, but feeds the history-store panel (up to one year
of 1h bars) instead of the ~7.5-day live panel — a full
mine → screen → backtest → overfit-gate cycle completes in minutes instead
of days of wall-clock waiting.

Per-candidate pipeline (mirrors orchestrator._tick_mine / _tick_evaluate):
1. FactorMiner.mine_factors(panel=hist_panel)   — DeepSeek generates
2. FactorScreen.screen()                        — quick IC / random-control
3. FactorStore.store()                          — persist to zoo + registry
4. AutoBacktester.run_backtest_for_factor()     — 1-year daily backtest
5. OverfitGate.evaluate()                       — 3-gate + HLZ multi-testing

Candidates that pass every gate are reported with their backtest metrics
and written to ``~/.vibe-trading/reports/replay_mine_<ts>.json``.  The
script never touches the running autopilot: lifecycle advancement of
passing candidates into paper trading is a separate, follow-up step.

Known limitation: factor dedup (orchestrator._factor_dedup_check) compares
against in-memory active factors and is skipped here — the zoo may hold
redundant factors until the dedup check runs in the autopilot.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# --- Bootstrap: make `src.*` importable and run from the agent root. -------
AGENT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_ROOT))
os.chdir(AGENT_ROOT)

from src.providers.llm import _ensure_dotenv  # noqa: E402

_ensure_dotenv()

from src.crypto_autopilot.auto_backtest import AutoBacktester  # noqa: E402
from src.crypto_autopilot.config import load_autopilot_config  # noqa: E402
from src.crypto_autopilot.factor_miner import FactorMiner  # noqa: E402
from src.crypto_autopilot.factor_screen import FactorScreen  # noqa: E402
from src.crypto_autopilot.factor_store import FactorStore  # noqa: E402
from src.crypto_autopilot.history_store import HistoryStore  # noqa: E402
from src.crypto_autopilot.llm_budget import LLMBudget  # noqa: E402
from src.crypto_autopilot.overfit_gate import OverfitGate  # noqa: E402
from src.crypto_autopilot.types import FactorCandidate  # noqa: E402

logger = logging.getLogger("replay_mine")

#: Report directory (same family as alpha_bench reports).
_REPORT_DIR = Path.home() / ".vibe-trading" / "reports"


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def execute_factor(candidate: FactorCandidate, panel: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    """Execute a candidate's ``compute(panel)`` from its full module source.

    Mirrors ``orchestrator._execute_factor``; best-effort, returns ``None``
    on any error so the loop keeps going.
    """
    full_source = candidate.meta.get("full_module_source", "")
    if not full_source:
        return None
    try:
        mod = types.ModuleType(f"_replay_{candidate.alpha_id}")
        mod.__dict__["__builtins__"] = __builtins__  # type: ignore[index]
        exec(compile(full_source, f"<{candidate.alpha_id}>", "exec"), mod.__dict__)
        compute_fn = getattr(mod, "compute", None)
        if compute_fn is None or not callable(compute_fn):
            return None
        return compute_fn(panel)
    except Exception as exc:  # noqa: BLE001
        logger.debug("execute_factor: %s failed: %s", candidate.alpha_id, exc)
        return None


def _metrics_subset(metrics: dict) -> dict:
    """Pick the human-readable slice of backtest metrics for the report."""
    keys = ("sharpe", "annual_return", "max_drawdown", "win_rate", "total_trades")
    return {k: metrics.get(k) for k in keys if k in metrics}


def run_round(
    *,
    miner: FactorMiner,
    screen: FactorScreen,
    store: FactorStore,
    backtester: AutoBacktester,
    gate: OverfitGate,
    panel: dict[str, pd.DataFrame],
    n_candidates: int,
    round_idx: int,
) -> dict:
    """Run one mine → screen → store → backtest → gate cycle."""
    t0 = time.monotonic()
    candidates = miner.mine_factors(panel=panel, n_candidates=n_candidates)
    if not candidates:
        logger.warning("round %d: no candidates mined", round_idx)
        return {"round": round_idx, "mined": 0, "passed_screen": [], "backtests": [], "elapsed_s": 0.0}

    return_df = FactorScreen.compute_returns(panel)
    passing: list[FactorCandidate] = []
    screened: list[dict] = []
    for candidate in candidates:
        factor_df = execute_factor(candidate, panel)
        if factor_df is None:
            screened.append({"alpha_id": candidate.alpha_id, "passed": False, "reason": "execute failed"})
            continue
        metrics = screen.screen(factor_df, return_df)
        rec = {"alpha_id": candidate.alpha_id, **{k: metrics.get(k) for k in ("ic_mean", "ic_positive_ratio", "alpha_t", "turnover_mean")}}
        if metrics.get("pass_screen"):
            candidate.meta["screen_ic_mean"] = metrics.get("ic_mean", 0.0)
            rec["passed"] = True
            passing.append(candidate)
        else:
            rec["passed"] = False
            rec["reason"] = "screen failed"
        screened.append(rec)
        logger.info(
            "round %d: %s screen=%s ic_mean=%.4f alpha_t=%.2f",
            round_idx, candidate.alpha_id, rec["passed"], rec.get("ic_mean", 0.0), rec.get("alpha_t", 0.0),
        )

    backtests: list[dict] = []
    for candidate in passing:
        try:
            store.store(candidate)
        except Exception as exc:  # noqa: BLE001
            logger.warning("round %d: store failed for %s: %s", round_idx, candidate.alpha_id, exc)
            backtests.append({"alpha_id": candidate.alpha_id, "status": "store_failed", "error": str(exc)})
            continue
        report = backtester.run_backtest_for_factor(candidate, panel)
        if report.status != "ok":
            backtests.append({
                "alpha_id": candidate.alpha_id,
                "status": report.status,
                "error": report.metrics.get("error", "unknown"),
            })
            logger.warning("round %d: backtest error for %s: %s", round_idx, candidate.alpha_id, report.metrics.get("error"))
            continue
        passes, reason, details = gate.evaluate(candidate, report)
        backtests.append({
            "alpha_id": candidate.alpha_id,
            "status": "passed" if passes else "rejected",
            "reason": reason,
            "metrics": _metrics_subset(report.metrics),
            "validation": {k: v for k, v in (report.validation or {}).items() if isinstance(v, (int, float, str, bool)) or v is None},
            "run_dir": report.run_dir,
        })
        logger.info(
            "round %d: %s gate=%s (%s)",
            round_idx, candidate.alpha_id, "PASS" if passes else "reject", reason,
        )

    elapsed = round(time.monotonic() - t0, 1)
    logger.info("round %d done in %.1fs: %d mined, %d screened, %d backtested", round_idx, elapsed, len(candidates), len(passing), len(backtests))
    return {
        "round": round_idx,
        "mined": len(candidates),
        "passed_screen": [c.alpha_id for c in passing],
        "screened": screened,
        "backtests": backtests,
        "elapsed_s": elapsed,
    }


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=3, help="mining rounds (default 3)")
    parser.add_argument("--n-candidates", type=int, default=3, help="candidates per round (default 3)")
    parser.add_argument("--bars", type=int, default=1440, help="history bars per symbol (default 1440 = 60d of 1h; 8760 ≈ 1y)")
    parser.add_argument("--pause", type=float, default=2.0, help="pause between rounds, seconds (default 2)")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_autopilot_config()
    history = HistoryStore()
    panel = history.get_panel(config.pairs, period=config.bar_period, bars=args.bars)
    if not panel or "close" not in panel or panel["close"].empty:
        logger.error("history panel empty — run the autopilot for a while or check ~/.vibe-trading/data/history/")
        return 1

    close = panel["close"]
    logger.info(
        "history panel: %d symbols x %d bars (%s .. %s, %s)",
        close.shape[1], close.shape[0], close.index[0], close.index[-1], config.bar_period,
    )

    miner = FactorMiner(model_name=config.deepseek_model, budget=LLMBudget())
    screen = FactorScreen()
    store = FactorStore()
    backtester = AutoBacktester(bars_per_year=config.bars_per_year)
    gate = OverfitGate()

    rounds: list[dict] = []
    for idx in range(1, args.rounds + 1):
        logger.info("=== round %d/%d ===", idx, args.rounds)
        rounds.append(run_round(
            miner=miner, screen=screen, store=store,
            backtester=backtester, gate=gate,
            panel=panel, n_candidates=args.n_candidates, round_idx=idx,
        ))
        if idx < args.rounds:
            time.sleep(args.pause)

    passed = [bt for r in rounds for bt in r["backtests"] if bt.get("status") == "passed"]
    summary = {
        "generated_at": _utc_now().isoformat(),
        "mode": "historical-replay-mine",
        "panel": {
            "period": config.bar_period,
            "bars": int(close.shape[0]),
            "start": str(close.index[0]),
            "end": str(close.index[-1]),
            "symbols": list(close.columns),
        },
        "rounds": rounds,
        "summary": {
            "total_mined": sum(r["mined"] for r in rounds),
            "passed_screen": sum(len(r["passed_screen"]) for r in rounds),
            "passed_gate": len(passed),
            "rejected_by_gate": sum(1 for r in rounds for bt in r["backtests"] if bt.get("status") == "rejected"),
            "backtest_errors": sum(1 for r in rounds for bt in r["backtests"] if bt.get("status") not in ("passed", "rejected")),
        },
    }

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _REPORT_DIR / f"replay_mine_{_utc_now().strftime('%Y%m%dT%H%M%SZ')}.json"
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\n===== REPLAY MINE SUMMARY =====")
    print(f"panel: {close.shape[1]} symbols x {close.shape[0]} bars ({close.index[0].date()} .. {close.index[-1].date()})")
    print(f"mined: {summary['summary']['total_mined']}  screen-passed: {summary['summary']['passed_screen']}")
    print(f"gate-passed: {summary['summary']['passed_gate']}  rejected: {summary['summary']['rejected_by_gate']}  errors: {summary['summary']['backtest_errors']}")
    if passed:
        print("\ngate-passed factors (candidates for paper trading):")
        for bt in passed:
            print(f"  - {bt['alpha_id']}: {bt.get('metrics')}")
    print(f"\nfull report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
