"""Pre-backtest screening for mined factor candidates.

Lightweight IC / turnover / random-control checks run *before* a full
backtest so obviously non-predictive candidates are discarded cheaply.
This mirrors the strict gate logic from
:mod:`src.factors.bench_runner_strict` but keeps it vectorised and
side-effect-free — no registry loads, no file I/O.

Design contract
---------------
:func:`FactorScreen.screen` takes a raw factor DataFrame (output of the
candidate's ``compute(panel)``) and a forward-return DataFrame of the same
shape, then returns a metrics dict with a boolean ``pass_screen`` flag.
The random-control comparison (signal IC vs shuffled IC) reuses
:func:`bench_runner_strict.compute_random_ic_series` so the screening
threshold is calibrated against the same baseline the production bench
runner uses.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.factors.bench_runner_strict import compute_random_ic_series
from src.factors.factor_analysis_core import compute_ic_series

__all__ = ["FactorScreen"]

logger = logging.getLogger(__name__)

#: Minimum |mean IC| to pass the screen.
_IC_MEAN_THRESHOLD: float = 0.02

#: Minimum fraction of dates with positive IC.
_IC_POSITIVE_RATIO_THRESHOLD: float = 0.5

#: Forward-return horizon (bars) used by the default screener.
_DEFAULT_HORIZON: int = 5

#: Minimum alpha_t (signal IC − random IC, t-stat) to beat the random control.
_MIN_ALPHA_T: float = 2.0


class FactorScreen:
    """Quick IC / turnover / random-control screener for mined factors.

    The screen is intentionally cheap: it operates on already-computed
    factor and return matrices (``pd.DataFrame``, index=date,
    columns=instrument) without touching the factor registry or the panel
    loader.  Candidates that fail are expected to be discarded before the
    expensive full backtest runs.
    """

    def screen(
        self,
        factor_df: pd.DataFrame,
        return_df: pd.DataFrame,
        *,
        n_random_seeds: int = 5,
        base_seed: int = 42,
    ) -> dict:
        """Compute IC metrics and decide whether *factor_df* passes screening.

        Args:
            factor_df: Factor values, index=date, columns=instruments.
            return_df: Forward returns, same shape as *factor_df*.
            n_random_seeds: Number of row-shuffled random controls.
            base_seed: Base RNG seed for the random controls.

        Returns:
            Metrics dict with keys::

                ic_mean, ic_std, ic_ir, ic_positive_ratio,
                ic_t_stat, turnover_mean, decay_mean,
                random_ic_mean, alpha_t, pass_screen
        """
        ic_series = compute_ic_series(factor_df, return_df)

        if ic_series.empty:
            logger.info("FactorScreen: empty IC series — failing")
            return self._empty_metrics()

        ic_mean = float(ic_series.mean())
        ic_std = float(ic_series.std(ddof=1)) if len(ic_series) > 1 else 0.0
        ic_ir = float(ic_mean / ic_std) if ic_std > 0 else 0.0
        ic_positive_ratio = float((ic_series > 0).mean())
        ic_t = self._t_stat(ic_series)

        turnover = self._compute_turnover(factor_df)
        decay = self._compute_decay_autocorr(factor_df)

        random_ic = compute_random_ic_series(
            factor_df,
            return_df,
            n_seeds=n_random_seeds,
            base_seed=base_seed,
        )
        random_ic_mean = float(random_ic.mean()) if not random_ic.empty else 0.0
        paired = (ic_series - random_ic).dropna()
        alpha_t = self._t_stat(paired)

        pass_screen = self._passes(
            ic_mean=ic_mean,
            ic_positive_ratio=ic_positive_ratio,
            alpha_t=alpha_t,
        )

        return {
            "ic_mean": ic_mean,
            "ic_std": ic_std,
            "ic_ir": ic_ir,
            "ic_positive_ratio": ic_positive_ratio,
            "ic_t_stat": ic_t,
            "turnover_mean": turnover,
            "decay_mean": decay,
            "random_ic_mean": random_ic_mean,
            "alpha_t": alpha_t,
            "pass_screen": pass_screen,
        }

    @staticmethod
    def compute_returns(panel: dict[str, pd.DataFrame], horizon: int = _DEFAULT_HORIZON) -> pd.DataFrame:
        """Forward returns from the close price panel.

        Args:
            panel: Factor panel keyed by column name (must contain
                ``"close"``).
            horizon: Forward-return horizon in bars.  Default 5.

        Returns:
            DataFrame of forward returns, same shape as ``panel["close"]``.
            The last *horizon* rows are NaN (no future data).
        """
        close = panel["close"]
        # Forward return: close.shift(-h) / close - 1.
        # Using shift(-h) on the raw close is a lookahead *for the return
        # target*, which is correct — we are labelling today with the
        # realised future return.  The factor itself never sees this.
        fwd = close.shift(-horizon) / close - 1.0
        return fwd

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _t_stat(series: pd.Series) -> float:
        """One-sample t-stat against zero.

        Args:
            series: Numeric series.

        Returns:
            t-statistic, or 0.0 if undefined (n < 2 or zero std).
        """
        n = len(series)
        if n < 2:
            return 0.0
        std = float(series.std(ddof=1))
        if not (std > 0 and np.isfinite(std)):
            return 0.0
        return float(series.mean() / (std / np.sqrt(n)))

    @staticmethod
    def _compute_turnover(factor_df: pd.DataFrame) -> float:
        """Mean cross-sectional turnover (rank-changed share) per bar.

        Vectorised: rank each bar, count how many instruments changed rank
        band, divide by universe size.

        Args:
            factor_df: Factor values, index=date, columns=instruments.

        Returns:
            Mean turnover in [0, 1], or 0.0 if undefined.
        """
        if factor_df.empty or len(factor_df) < 2:
            return 0.0
        ranks = factor_df.rank(axis=1, method="average", pct=True, na_option="keep")
        diff = ranks.diff().abs()
        # Turnover = mean absolute rank change; bounded in [0, 1].
        turnover = diff.mean(axis=1, skipna=True)
        valid = turnover.dropna()
        return float(valid.mean()) if not valid.empty else 0.0

    @staticmethod
    def _compute_decay_autocorr(factor_df: pd.DataFrame) -> float:
        """Mean bar-to-bar rank autocorrelation of the factor.

        A high value (> 0.5) indicates the factor signal decays slowly
        (good); near-zero indicates high turnover (noisy).

        Vectorised: ranks each bar, then computes the Pearson correlation
        between the flattened rank matrix and its 1-bar lag.

        Args:
            factor_df: Factor values, index=date, columns=instruments.

        Returns:
            Mean autocorrelation in [-1, 1], or 0.0 if undefined.
        """
        if factor_df.empty or len(factor_df) < 2:
            return 0.0
        ranks = factor_df.rank(axis=1, method="average", pct=True, na_option="keep")
        shifted = ranks.shift(1)
        # Flatten both frames to 1-D vectors of paired (today, yesterday) rank
        # values, drop NaN pairs, and compute the Pearson correlation.
        today = ranks.to_numpy().ravel()
        yest = shifted.to_numpy().ravel()
        mask = ~(np.isnan(today) | np.isnan(yest))
        if mask.sum() < 2:
            return 0.0
        a = today[mask]
        b = yest[mask]
        with np.errstate(invalid="ignore"):
            std_a = a.std(ddof=1)
            std_b = b.std(ddof=1)
            if not (std_a > 0 and std_b > 0):
                return 0.0
            corr = float(np.corrcoef(a, b)[0, 1])
        return corr if np.isfinite(corr) else 0.0

    @staticmethod
    def _passes(
        *,
        ic_mean: float,
        ic_positive_ratio: float,
        alpha_t: float,
    ) -> bool:
        """Apply the screening thresholds.

        Args:
            ic_mean: Mean rank IC.
            ic_positive_ratio: Fraction of dates with IC > 0.
            alpha_t: t-stat of (signal IC − random IC).

        Returns:
            ``True`` if the candidate passes all thresholds.
        """
        if abs(ic_mean) <= _IC_MEAN_THRESHOLD:
            return False
        if ic_positive_ratio <= _IC_POSITIVE_RATIO_THRESHOLD:
            return False
        if alpha_t < _MIN_ALPHA_T:
            return False
        return True

    @staticmethod
    def _empty_metrics() -> dict:
        """Return a failing metrics dict for empty/undefined IC."""
        return {
            "ic_mean": 0.0,
            "ic_std": 0.0,
            "ic_ir": 0.0,
            "ic_positive_ratio": 0.0,
            "ic_t_stat": 0.0,
            "turnover_mean": 0.0,
            "decay_mean": 0.0,
            "random_ic_mean": 0.0,
            "alpha_t": 0.0,
            "pass_screen": False,
        }
