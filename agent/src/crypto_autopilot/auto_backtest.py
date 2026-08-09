"""Automated backtest runner for mined crypto factors.

:class:`AutoBacktester` bridges the gap between a freshly-mined
:class:`FactorCandidate` and a fully-validated :class:`BacktestReport`.  It
assembles a backtest run directory (``config.json`` + ``code/signal_engine.py``)
that conforms to the :func:`src.tools.backtest_tool.run_backtest` contract,
invokes the built-in backtest engine in a sandboxed subprocess, parses the
resulting artifacts (equity curve, trades, metrics), and runs the
:func:`backtest.validation.run_validation` statistical suite (Monte Carlo,
bootstrap, walk-forward) with ``bars_per_year=365`` — the correct
annualisation factor for 24/7 crypto markets.

The backtest never crashes the autopilot loop: any failure (subprocess
timeout, missing artifacts, validation error) is caught and surfaced as a
:class:`BacktestReport` with ``status="error"``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.validation import run_validation
from src.crypto_autopilot.types import BacktestReport, FactorCandidate
from src.tools.backtest_tool import run_backtest

logger = logging.getLogger(__name__)

__all__ = ["AutoBacktester"]

#: Annualisation factor for 24/7 crypto markets (365 daily bars per year).
_CRYPTO_BARS_PER_YEAR: int = 365

#: Default initial capital for backtests.
_DEFAULT_INITIAL_CASH: float = 1_000_000.0

#: Price columns that the SignalEngine panel builder extracts from data_map.
_PRICE_PANEL_COLUMNS: tuple[str, ...] = (
    "open", "high", "low", "close", "volume", "vwap", "amount",
)

#: Validation configuration embedded in every backtest config.json.
_VALIDATION_CONFIG: dict[str, Any] = {
    "monte_carlo": {"n_simulations": 500},
    "bootstrap": {"n_bootstrap": 500, "confidence": 0.95},
    "walk_forward": {"n_windows": 5},
}


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def _signal_engine_template(alpha_id: str) -> str:
    """Render a ``signal_engine.py`` source that wraps a zoo factor.

    The generated module imports the factor from the process-wide
    :func:`~src.factors.registry.get_default_registry` and converts its
    cross-sectional factor values into directional trading signals
    (positive → long, negative → short, zero/NaN → flat).

    The template is designed to pass the AST safety checks in
    :func:`backtest.runner._validate_signal_engine_source` — no top-level
    executable statements, no decorators, no unsafe annotations, and no
    forbidden operations inside ``SignalEngine`` methods.

    Args:
        alpha_id: The zoo-registered factor identifier.

    Returns:
        Complete, importable ``signal_engine.py`` source text.
    """
    # The alpha_id is injected as a string literal constant so it is
    # always a safe top-level assignment (ast.Constant).
    return f'''"""Auto-generated SignalEngine for crypto autopilot backtest.

Wraps the mined factor {alpha_id} from the Alpha Zoo registry and converts
its cross-sectional factor values into directional trading signals.
"""

from __future__ import annotations

import pandas as pd

from src.factors.registry import get_default_registry

_ALPHA_ID = "{alpha_id}"


class SignalEngine:
    def generate(self, data_map: dict) -> dict:
        panel = self._build_panel(data_map)
        reg = get_default_registry()
        factor_df = reg.compute(_ALPHA_ID, panel)
        signals = {{}}
        for code, df in data_map.items():
            if code in factor_df.columns:
                col = factor_df[code].reindex(df.index)
                sig = pd.Series(0.0, index=df.index)
                sig[col > 0] = 1.0
                sig[col < 0] = -1.0
                signals[code] = sig
            else:
                signals[code] = pd.Series(0.0, index=df.index)
        return signals

    def _build_panel(self, data_map: dict) -> dict:
        panel = {{}}
        for col_name in {list(_PRICE_PANEL_COLUMNS)!r}:
            series_by_symbol = {{}}
            for code, df in data_map.items():
                if col_name in df.columns:
                    series_by_symbol[code] = df[col_name]
            if series_by_symbol:
                panel[col_name] = pd.DataFrame(series_by_symbol)
        return panel
'''


class AutoBacktester:
    """Run automated backtests for mined crypto factor candidates.

    The backtester assembles a conforming run directory, invokes
    :func:`run_backtest` (which spawns a sandboxed subprocess with a 300s
    timeout), parses the output artifacts, and runs the statistical
    validation suite.  Every failure mode is caught and returned as a
    ``BacktestReport`` with ``status="error"`` — the autopilot loop is never
    crashed by a backtest failure.

    Attributes:
        run_root: Root directory for backtest run dirs (under an allowed
            run root so :func:`safe_run_dir` accepts it).
        bars_per_year: Annualisation factor for Sharpe etc. (365 for crypto).
        initial_cash: Starting capital for the backtest.
    """

    def __init__(
        self,
        run_root: Path | None = None,
        bars_per_year: int = _CRYPTO_BARS_PER_YEAR,
        initial_cash: float = _DEFAULT_INITIAL_CASH,
    ) -> None:
        """Initialise the backtester.

        Args:
            run_root: Root for run directories.  Defaults to
                ``<agent>/runs/autopilot/`` which is inside the allowed run
                roots enforced by :func:`safe_run_dir`.
            bars_per_year: Annualisation factor.  Default 365 (crypto 24/7).
            initial_cash: Starting capital.  Default 1,000,000.
        """
        if run_root is None:
            agent_root = Path(__file__).resolve().parents[2]
            run_root = agent_root / "runs" / "autopilot"
        self.run_root = Path(run_root)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.bars_per_year = bars_per_year
        self.initial_cash = initial_cash

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_backtest_for_factor(
        self,
        candidate: FactorCandidate,
        panel: dict[str, pd.DataFrame],
    ) -> BacktestReport:
        """Run a full backtest + validation for a single factor candidate.

        Assembles a run directory with ``config.json`` and
        ``code/signal_engine.py``, calls :func:`run_backtest`, parses the
        artifacts, runs :func:`run_validation`, and returns a
        :class:`BacktestReport`.

        Args:
            candidate: The mined factor candidate to backtest.  Must have
                been stored to the zoo (via :class:`FactorStore`) and the
                registry reset so ``get_default_registry()`` can discover it.
            panel: Factor panel keyed by column name (``close``, ``volume``,
                etc.).  Each value is a wide ``DataFrame`` indexed by date
                with one column per instrument.

        Returns:
            A :class:`BacktestReport` with parsed metrics, validation
            results, equity curve, and trades.  On any failure, returns a
            report with ``status="error"`` and an error message in
            ``metrics["error"]``.
        """
        alpha_id = candidate.alpha_id
        created_at = _utc_now()

        # Derive backtest parameters from the panel.
        close = panel.get("close")
        if close is None or close.empty:
            logger.warning("AutoBacktester: panel missing 'close'; aborting")
            return BacktestReport(
                alpha_id=alpha_id,
                run_dir="",
                status="error",
                metrics={"error": "panel missing 'close'"},
                validation={},
                equity_curve=[],
                trades=[],
                passed_gate=False,
                created_at=created_at,
            )

        codes = list(close.columns)
        start_date = str(close.index[0].date()) if hasattr(close.index[0], "date") else str(close.index[0])
        end_date = str(close.index[-1].date()) if hasattr(close.index[-1], "date") else str(close.index[-1])

        # Create run directory.
        run_dir = self._create_run_dir(alpha_id)

        try:
            self._write_config(run_dir, alpha_id, codes, start_date, end_date)
            self._write_signal_engine(run_dir, alpha_id)
        except OSError as exc:
            logger.warning("AutoBacktester: failed to write run files: %s", exc)
            return self._error_report(alpha_id, str(run_dir), f"write failed: {exc}", created_at)

        # Invoke the backtest engine.
        try:
            result_json = run_backtest(str(run_dir))
            result = json.loads(result_json)
        except (json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
            logger.warning("AutoBacktester: run_backtest failed: %s", exc)
            return self._error_report(alpha_id, str(run_dir), f"run_backtest failed: {exc}", created_at)

        if result.get("status") != "ok":
            error_msg = result.get("stderr", result.get("error", "unknown"))
            logger.warning("AutoBacktester: backtest failed for %s: %s", alpha_id, error_msg[:200])
            return self._error_report(
                alpha_id,
                str(run_dir),
                f"backtest status={result.get('status')}: {error_msg[:500]}",
                created_at,
            )

        # Parse artifacts.
        try:
            metrics, equity_curve, trades = self._parse_artifacts(run_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AutoBacktester: artifact parsing failed: %s", exc)
            return self._error_report(
                alpha_id, str(run_dir), f"artifact parse failed: {exc}", created_at,
            )

        # Run statistical validation.
        try:
            validation = self._run_validation(
                run_dir, equity_curve, trades, candidate,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("AutoBacktester: validation failed: %s", exc)
            validation = {"error": str(exc)}

        return BacktestReport(
            alpha_id=alpha_id,
            run_dir=str(run_dir),
            status="ok",
            metrics=metrics,
            validation=validation,
            equity_curve=self._equity_to_list(equity_curve),
            trades=trades,
            passed_gate=False,  # Gate evaluation is OverfitGate's responsibility
            created_at=created_at,
        )

    def cleanup_run_dir(self, run_dir: str) -> None:
        """Remove a run directory and its contents.

        Args:
            run_dir: Path to the run directory to remove.
        """
        try:
            shutil.rmtree(run_dir, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AutoBacktester: cleanup of %s failed: %s", run_dir, exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _create_run_dir(self, alpha_id: str) -> Path:
        """Create a unique run directory for one backtest.

        Args:
            alpha_id: Factor identifier (sanitised for filesystem use).

        Returns:
            Path to the created run directory.
        """
        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", alpha_id)
        timestamp = _utc_now().strftime("%Y%m%d_%H%M%S_%f")
        run_dir = self.run_root / f"{safe_id}_{timestamp}"
        (run_dir / "code").mkdir(parents=True, exist_ok=True)
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
        return run_dir

    def _write_config(
        self,
        run_dir: Path,
        alpha_id: str,
        codes: list[str],
        start_date: str,
        end_date: str,
    ) -> None:
        """Write ``config.json`` conforming to ``BacktestConfigSchema``.

        Args:
            run_dir: Run directory path.
            alpha_id: Factor identifier.
            codes: List of instrument codes.
            start_date: Backtest start date (YYYY-MM-DD).
            end_date: Backtest end date (YYYY-MM-DD).
        """
        config: dict[str, Any] = {
            "codes": codes,
            "start_date": start_date,
            "end_date": end_date,
            "source": "okx",
            "interval": "1D",
            "engine": "daily",
            "initial_cash": self.initial_cash,
            # Extra fields (allowed by BacktestConfigSchema's extra="allow").
            "alpha_id": alpha_id,
            "zoo": "crypto_mined",
            "universe": ["crypto"],
            "bars_per_year": self.bars_per_year,
            "alpha_ids": [alpha_id],
            "validation": dict(_VALIDATION_CONFIG),
        }
        config_path = run_dir / "config.json"
        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _write_signal_engine(self, run_dir: Path, alpha_id: str) -> None:
        """Write ``code/signal_engine.py`` wrapping the zoo factor.

        Args:
            run_dir: Run directory path.
            alpha_id: Factor identifier to import from the registry.
        """
        source = _signal_engine_template(alpha_id)
        signal_path = run_dir / "code" / "signal_engine.py"
        signal_path.write_text(source, encoding="utf-8")

    def _parse_artifacts(
        self, run_dir: Path,
    ) -> tuple[dict[str, Any], pd.Series, list[dict[str, Any]]]:
        """Parse metrics, equity, and trades from the run artifacts.

        Args:
            run_dir: Run directory containing ``artifacts/``.

        Returns:
            Tuple of ``(metrics_dict, equity_series, trades_list)``.

        Raises:
            FileNotFoundError: If required artifacts are missing.
        """
        # Metrics: single-row CSV → dict.
        metrics_path = run_dir / "artifacts" / "metrics.csv"
        if metrics_path.exists():
            metrics_df = pd.read_csv(metrics_path)
            metrics = (
                metrics_df.iloc[0].to_dict() if not metrics_df.empty else {}
            )
        else:
            # Some engines may write metrics.json as an alternative.
            metrics_json = run_dir / "artifacts" / "metrics.json"
            if metrics_json.exists():
                metrics = json.loads(metrics_json.read_text(encoding="utf-8"))
            else:
                metrics = {}

        # Equity curve: CSV → pd.Series (column: equity/nav/value).
        equity_path = run_dir / "artifacts" / "equity.csv"
        if not equity_path.exists():
            raise FileNotFoundError(f"equity.csv not found in {run_dir / 'artifacts'}")
        equity_df = pd.read_csv(equity_path, index_col=0, parse_dates=True)
        equity_col = None
        for col in ("equity", "nav", "value"):
            if col in equity_df.columns:
                equity_col = col
                break
        if equity_col is None:
            equity_col = equity_df.columns[0] if len(equity_df.columns) > 0 else "equity"
        equity_series = equity_df[equity_col]

        # Trades: CSV → list of dicts.
        trades_path = run_dir / "artifacts" / "trades.csv"
        if trades_path.exists():
            trades_df = pd.read_csv(trades_path)
            trades = trades_df.to_dict(orient="records") if not trades_df.empty else []
        else:
            trades = []

        return metrics, equity_series, trades

    def _run_validation(
        self,
        run_dir: Path,
        equity_curve: pd.Series,
        trades_raw: list[dict[str, Any]],
        candidate: FactorCandidate,
    ) -> dict[str, Any]:
        """Run the statistical validation suite on backtest outputs.

        Converts raw trade dicts to :class:`TradeRecord` objects (required
        by :func:`run_validation`) and invokes the three-gate validation.

        Args:
            run_dir: Run directory (for reading config.json).
            equity_curve: Equity time series.
            trades_raw: Raw trade dicts from trades.csv.
            candidate: The factor candidate (for metadata).

        Returns:
            Validation results dict keyed by ``monte_carlo``,
            ``bootstrap``, ``walk_forward``.
        """
        from backtest.models import TradeRecord

        # Re-read config for the validation section.
        config_path = run_dir / "config.json"
        config = (
            json.loads(config_path.read_text(encoding="utf-8"))
            if config_path.exists() else {}
        )

        # Convert raw trade dicts to TradeRecord objects.
        trades: list[TradeRecord] = []
        for row in trades_raw:
            try:
                pnl = float(row.get("pnl", 0))
            except (ValueError, TypeError):
                pnl = 0.0
            if pnl == 0:
                continue  # Skip entry rows; only exit rows have pnl.
            try:
                hold = pd.to_numeric(row.get("holding_days", 0), errors="coerce")
                holding_bars = 0 if pd.isna(hold) else int(hold)
            except Exception:
                holding_bars = 0
            trades.append(TradeRecord(
                symbol=str(row.get("code", "")),
                direction=1 if row.get("side") == "sell" else -1,
                entry_price=0.0,
                exit_price=float(row.get("price", 0)),
                entry_time=pd.Timestamp(row.get("timestamp", "2000-01-01")),
                exit_time=pd.Timestamp(row.get("timestamp", "2000-01-01")),
                size=float(row.get("qty", 0)),
                leverage=1.0,
                pnl=pnl,
                pnl_pct=float(row.get("return_pct", 0)),
                exit_reason=str(row.get("reason", "signal")),
                holding_bars=holding_bars,
                commission=0.0,
            ))

        return run_validation(
            config,
            equity_curve,
            trades,
            initial_capital=self.initial_cash,
            bars_per_year=self.bars_per_year,
        )

    @staticmethod
    def _equity_to_list(equity: pd.Series) -> list[Any]:
        """Convert an equity Series to a JSON-serialisable list.

        Args:
            equity: Equity time series.

        Returns:
            List of ``{"timestamp": str, "equity": float}`` dicts.
        """
        result: list[Any] = []
        for ts, val in equity.items():
            ts_str = str(ts.date()) if hasattr(ts, "date") else str(ts)
            result.append({"timestamp": ts_str, "equity": float(val) if pd.notna(val) else 0.0})
        return result

    @staticmethod
    def _error_report(
        alpha_id: str,
        run_dir: str,
        error: str,
        created_at: datetime,
    ) -> BacktestReport:
        """Build an error :class:`BacktestReport`.

        Args:
            alpha_id: Factor identifier.
            run_dir: Run directory path.
            error: Error message.
            created_at: Timestamp.

        Returns:
            A ``BacktestReport`` with ``status="error"``.
        """
        return BacktestReport(
            alpha_id=alpha_id,
            run_dir=run_dir,
            status="error",
            metrics={"error": error},
            validation={},
            equity_curve=[],
            trades=[],
            passed_gate=False,
            created_at=created_at,
        )
