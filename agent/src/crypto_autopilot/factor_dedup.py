"""Factor IC-series deduplication for the crypto autopilot.

A mined candidate can be *code-different* yet trade the same latent
signal as an already-active factor. Before a candidate advances to paper
trading, :func:`dedup_rejection_reason` compares its IC time series
(cross-sectional correlation of factor values with next-bar returns,
computed per bar on the long history window) against every active
factor's IC series. A |ρ| above ``threshold`` (default 0.7) rejects the
candidate with a recorded reason — the panel's retired/拒绝 section
surfaces it.

The long history window is what makes this meaningful: on the ~7.5-day
live panel two IC series trivially correlate, while the 60-day history
store gives the redundancy test statistical power.
"""

from __future__ import annotations

import logging
from typing import Iterable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "compute_ic_series",
    "ic_series_correlation",
    "dedup_rejection_reason",
    "DEFAULT_MAX_FACTOR_CORRELATION",
]

#: Default |ρ| threshold above which two IC series count as redundant.
DEFAULT_MAX_FACTOR_CORRELATION: float = 0.7

#: Minimum number of overlapping IC observations before a correlation is
#: trustworthy; below this the pair is treated as uncorrelated.
_MIN_OVERLAP_BARS: int = 20


def compute_ic_series(
    close: pd.DataFrame,
    factor_df: pd.DataFrame,
    horizon: int = 1,
) -> pd.Series:
    """Cross-sectional IC per bar: corr(factor values, next-bar returns).

    For every bar with at least two symbols carrying both a factor value
    and a forward return, the Pearson correlation across symbols is one
    IC observation. The result is a time series of ICs aligned to the
    shared timestamps of ``close`` and ``factor_df``.

    Args:
        close: Wide close-price panel, DatetimeIndex x symbols.
        factor_df: Factor values, DatetimeIndex x symbols (symbol sets
            may differ from ``close``; only the intersection is used).
        horizon: Number of bars ahead for the return. Default 1.

    Returns:
        IC series (floats) with NaN observations dropped. The trailing
        ``horizon`` bars carry no forward return and the leading bar
        carries no past return, so they never appear as observations.
    """
    if close is None or factor_df is None:
        return pd.Series(dtype=float)
    idx = factor_df.index.intersection(close.index)
    if len(idx) == 0:
        return pd.Series(dtype=float)
    rets = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    fwd = rets.shift(-horizon)
    f = factor_df.loc[idx]
    r = fwd.loc[idx]
    ics: list[float] = []
    for ts in f.index:
        fv = f.loc[ts]
        rv = r.loc[ts]
        valid = fv.notna() & rv.notna()
        if int(valid.sum()) >= 2:
            corr = np.corrcoef(
                fv[valid].astype(float), rv[valid].astype(float)
            )[0, 1]
            ics.append(float(corr))
        else:
            ics.append(np.nan)
    # Drop bars without a usable IC (sparse cross-section or no forward
    # return) so downstream correlation never mixes NaN-aligned entries.
    return pd.Series(ics, index=f.index, dtype=float).dropna()


def ic_series_correlation(ic_a: pd.Series, ic_b: pd.Series) -> float:
    """Pearson correlation of two IC series over their shared timestamps.

    Returns ``0.0`` when the overlap is below :data:`_MIN_OVERLAP_BARS`
    (treating "not enough evidence" as "not correlated", never as a
    rejection trigger).

    Args:
        ic_a: First IC series from :func:`compute_ic_series`.
        ic_b: Second IC series.

    Returns:
        Correlation coefficient in [-1, 1] (0.0 on insufficient overlap).
    """
    joined = pd.concat([ic_a, ic_b], axis=1, keys=["a", "b"]).dropna()
    if len(joined) < _MIN_OVERLAP_BARS:
        return 0.0
    return float(np.corrcoef(joined["a"], joined["b"])[0, 1])


def dedup_rejection_reason(
    candidate_alpha_id: str,
    candidate_ic: pd.Series,
    active_entries: Iterable[tuple[str, pd.Series]],
    threshold: float = DEFAULT_MAX_FACTOR_CORRELATION,
) -> tuple[bool, str]:
    """Decide whether a candidate duplicates an active factor.

    Args:
        candidate_alpha_id: Alpha id of the candidate being evaluated.
        candidate_ic: IC series of the candidate (long history window).
        active_entries: ``(alpha_id, ic_series)`` pairs for every active
            factor that produced a valid IC series on the same window.
        threshold: |ρ| above which the pair is considered redundant.

    Returns:
        ``(rejected, reason)``. ``rejected`` is ``True`` when the
        candidate's IC series correlates beyond ``threshold`` with any
        active factor; ``reason`` names the first offending pair.
    """
    for other_id, other_ic in active_entries:
        rho = ic_series_correlation(candidate_ic, other_ic)
        if abs(rho) > threshold:
            reason = (
                f"IC correlation with active {other_id} = {rho:.2f} "
                f"(|ρ| > {threshold})"
            )
            logger.info(
                "dedup: %s rejected — %s", candidate_alpha_id, reason
            )
            return True, reason
    return False, ""
