r"""Return and risk statistics over a ``PriceHistory``.

Conventions (documented once, applied everywhere):

* **Total return**: :math:`S_T / S_0 - 1` over the observed window.
* **CAGR** (geometric annualized return): with :math:`n` return periods and
  :math:`p` periods per year,

  .. math::  \text{CAGR} = (S_T / S_0)^{p / n} - 1

  Annualization uses *observed periods*, not calendar days, so a series of
  daily bars annualizes with :math:`p = 252` regardless of weekends/holidays.
* **Annualized volatility**: sample standard deviation (ddof=1) of **log**
  returns scaled by :math:`\sqrt{p}`. Log returns are used because they are
  time-additive, which is what makes the :math:`\sqrt{p}` scaling exact under
  i.i.d. returns.
* **Sharpe ratio** (rf = 0): :math:`\sqrt{p}\,\bar{r} / s_r` on **simple**
  returns -- the conventional ex-post definition. Undefined (NaN) when the
  return variance is zero.
* **Max drawdown**: :math:`\min_t \left( S_t / \max_{u \le t} S_u - 1 \right)`,
  reported as a negative number (0 means the series never fell below a prior
  peak).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_hub.data.types import PriceHistory

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class ReturnStats:
    """Summary statistics for one price series. All values are decimal
    fractions (0.10 = 10%), not percentages."""

    n_observations: int
    total_return: float
    cagr: float
    annualized_vol: float
    sharpe: float
    max_drawdown: float


def max_drawdown(closes: pd.Series) -> float:
    """Largest peak-to-trough decline of ``closes``, as a negative fraction."""
    if len(closes) < 1:
        raise ValueError("max_drawdown requires at least one price")
    running_peak = closes.cummax()
    drawdowns = closes / running_peak - 1.0
    return float(drawdowns.min())


def summary_stats(
    history: PriceHistory, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> ReturnStats:
    """Compute ``ReturnStats`` for ``history``.

    Requires at least two observations (one return). ``periods_per_year``
    must match the bar frequency of the history (252 for daily bars).
    """
    closes = history.closes
    if len(closes) < 2:
        raise ValueError("summary_stats requires at least two prices")
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year must be >= 1, got {periods_per_year}")

    simple = history.simple_returns()
    log_ret = history.log_returns()
    n = len(simple)

    first = float(closes.iloc[0])
    last = float(closes.iloc[-1])
    total_return = last / first - 1.0
    cagr = (last / first) ** (periods_per_year / n) - 1.0

    annualized_vol = float(log_ret.std(ddof=1)) * math.sqrt(periods_per_year)

    simple_std = float(simple.std(ddof=1))
    if simple_std > 0 and np.isfinite(simple_std):
        sharpe = math.sqrt(periods_per_year) * float(simple.mean()) / simple_std
    else:
        sharpe = float("nan")

    return ReturnStats(
        n_observations=len(closes),
        total_return=total_return,
        cagr=cagr,
        annualized_vol=annualized_vol,
        sharpe=sharpe,
        max_drawdown=max_drawdown(closes),
    )
