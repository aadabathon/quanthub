r"""Return and risk analytics over price series.

This module is the application's quantitative vocabulary: every metric shown
anywhere in the GUI is computed here (or in ``data.simulation``), never in
widget code or plotting code. Functions operate on plain pandas objects so any
source — synthetic GBM, IBKR bars, future CSV files — flows through the same
pipeline via ``PriceHistory``.

Definitions (documented once, applied everywhere)
-------------------------------------------------
Let :math:`S_0, \dots, S_n` be the observed prices (:math:`n` return periods),
and :math:`p` the number of periods per year (252 for daily bars).

* **Simple return**: :math:`R_t = S_t / S_{t-1} - 1` (see
  ``PriceHistory.simple_returns``).
* **Log return**: :math:`r_t = \ln(S_t / S_{t-1})` (see
  ``PriceHistory.log_returns``). Log returns are time-additive
  (:math:`\sum r_t = \ln(S_n/S_0)`), which is what makes the
  :math:`\sqrt{p}` volatility scaling exact under i.i.d. returns, and they
  are the quantity that is exactly Gaussian under GBM.
* **Total return**: :math:`S_n / S_0 - 1` over the observed window.
* **Geometric mean return** (per period): :math:`(S_n/S_0)^{1/n} - 1` — the
  constant per-period return that compounds to the observed total. Always
  :math:`\le` the arithmetic mean return (AM–GM); the gap grows with variance.
* **CAGR**: :math:`(S_n/S_0)^{1/T} - 1` with the horizon :math:`T = n/p`
  measured in **years of observed periods** (a daily series annualizes with
  :math:`p = 252` regardless of weekends/holidays). Equivalently the
  geometric mean return compounded over one year:
  :math:`\text{CAGR} = (S_n/S_0)^{p/n} - 1 = e^{\bar{r} p} - 1`.
* **Sample volatility**: sample standard deviation (ddof=1) of log returns,
  per period. **Annualized volatility** multiplies by :math:`\sqrt{p}`.
* **Rolling volatility**: the same estimator over a trailing window of
  ``window`` returns, annualized identically. The window is a parameter —
  21 (≈ one trading month) is only a default, not an assumption.
* **Drawdown**: :math:`D_t = S_t / \max_{u \le t} S_u - 1 \le 0`;
  **max drawdown** is :math:`\min_t D_t`.
* **Sharpe ratio** (rf = 0): :math:`\sqrt{p}\,\bar{R} / s_R` on simple
  returns — the conventional ex-post definition. NaN when the return
  variance is zero.
* **Skewness / excess kurtosis** (of log returns, the GBM-Gaussian quantity):
  moment estimators with :math:`m_k = \frac{1}{n}\sum (x_i - \bar{x})^k`,

  .. math:: g_1 = m_3 / m_2^{3/2}, \qquad g_2 = m_4 / m_2^2 - 3

  Both are 0 for a normal distribution (in expectation), so under GBM they
  are diagnostics of pure sampling noise; under real data they reveal fat
  tails. The plain moment versions (not the bias-adjusted :math:`G_1, G_2`)
  are used so tests can verify them by hand; they are biased in small
  samples, which is itself part of the estimation-uncertainty story.

Estimating GBM parameters from a sample
---------------------------------------
Under GBM, log returns are i.i.d.
:math:`r_t \sim N\big((\mu - \tfrac{1}{2}\sigma^2)\,\Delta t,\; \sigma^2 \Delta t\big)`
with :math:`\Delta t = 1/p`. The natural estimators are therefore

.. math::
   \hat{\sigma} = s_r \sqrt{p}, \qquad
   \widehat{\mu - \tfrac{1}{2}\sigma^2} = \bar{r}\, p, \qquad
   \hat{\mu} = \bar{r}\, p + \tfrac{1}{2}\hat{\sigma}^2

i.e. :math:`\hat\mu` inverts the Ito correction to recover the **SDE drift**,
not the log-drift. Standard errors differ enormously:
:math:`\text{SE}(\hat\sigma) \approx \sigma/\sqrt{2n}` shrinks with the
number of observations, while :math:`\text{SE}(\hat\mu) \approx \sigma/\sqrt{T}`
shrinks only with the calendar length of the sample — drift is hard to
estimate no matter how finely you sample. That asymmetry is exactly what the
dashboard's true-vs-estimate panel makes visible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_hub.data.types import PriceHistory

#: The single home of the daily-bars annualization assumption. Every function
#: takes ``periods_per_year`` explicitly and merely defaults to this.
TRADING_DAYS_PER_YEAR = 252

#: Default rolling-volatility window: ~one trading month of daily bars.
DEFAULT_VOL_WINDOW = 21


# --------------------------------------------------------------------------
# Elementary metrics (each one small, testable against hand computation)
# --------------------------------------------------------------------------

def total_return(closes: pd.Series) -> float:
    """:math:`S_n / S_0 - 1` over the series."""
    if len(closes) < 2:
        raise ValueError("total_return requires at least two prices")
    return float(closes.iloc[-1] / closes.iloc[0]) - 1.0


def geometric_mean_return(closes: pd.Series) -> float:
    """Per-period geometric mean: :math:`(S_n/S_0)^{1/n} - 1` for n periods."""
    if len(closes) < 2:
        raise ValueError("geometric_mean_return requires at least two prices")
    n = len(closes) - 1
    return float(closes.iloc[-1] / closes.iloc[0]) ** (1.0 / n) - 1.0


def cagr(closes: pd.Series, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Compound annual growth rate.

    :math:`(S_n/S_0)^{p/n} - 1`, i.e. the horizon is ``n_returns /
    periods_per_year`` **years**. 253 daily prices = 252 return periods =
    exactly one year of trading days.
    """
    if len(closes) < 2:
        raise ValueError("cagr requires at least two prices")
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year must be >= 1, got {periods_per_year}")
    n = len(closes) - 1
    return float(closes.iloc[-1] / closes.iloc[0]) ** (periods_per_year / n) - 1.0


def annualized_volatility(
    log_returns: pd.Series, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    """Sample std (ddof=1) of log returns scaled by :math:`\\sqrt{p}`.

    NaN when fewer than two returns exist (the sample std is undefined).
    """
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year must be >= 1, got {periods_per_year}")
    if len(log_returns) < 2:
        return float("nan")
    return float(log_returns.std(ddof=1)) * math.sqrt(periods_per_year)


def rolling_volatility(
    log_returns: pd.Series,
    window: int = DEFAULT_VOL_WINDOW,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> pd.Series:
    """Trailing-window annualized volatility.

    Element :math:`t` is the sample std (ddof=1) of the ``window`` most recent
    log returns up to and including :math:`t`, times :math:`\\sqrt{p}`. The
    first ``window - 1`` elements are NaN (insufficient history) and are
    dropped, so the result starts once the estimator is fully defined.
    """
    if window < 2:
        raise ValueError(f"window must be >= 2, got {window}")
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year must be >= 1, got {periods_per_year}")
    rolled = log_returns.rolling(window, min_periods=window).std(ddof=1)
    return (rolled * math.sqrt(periods_per_year)).dropna()


def drawdown_series(closes: pd.Series) -> pd.Series:
    """:math:`D_t = S_t / \\max_{u \\le t} S_u - 1`, elementwise (all <= 0)."""
    if len(closes) < 1:
        raise ValueError("drawdown_series requires at least one price")
    return closes / closes.cummax() - 1.0


def max_drawdown(closes: pd.Series) -> float:
    """Largest peak-to-trough decline, as a negative fraction (0 = no decline)."""
    return float(drawdown_series(closes).min())


def skewness(x: pd.Series) -> float:
    """Moment estimator :math:`g_1 = m_3 / m_2^{3/2}`. NaN if undefined."""
    return _standardized_moment(x, 3)


def excess_kurtosis(x: pd.Series) -> float:
    """Moment estimator :math:`g_2 = m_4 / m_2^2 - 3`. NaN if undefined."""
    return _standardized_moment(x, 4) - 3.0


def _standardized_moment(x: pd.Series, k: int) -> float:
    """:math:`m_k / m_2^{k/2}` with central moments :math:`m_j`; NaN when
    fewer than two observations or zero variance."""
    values = x.to_numpy(dtype=float)
    if len(values) < 2:
        return float("nan")
    centered = values - values.mean()
    m2 = float(np.mean(centered**2))
    if m2 <= 0.0 or not np.isfinite(m2):
        return float("nan")
    mk = float(np.mean(centered**k))
    return mk / m2 ** (k / 2.0)


# --------------------------------------------------------------------------
# GBM parameter estimation
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class GBMEstimate:
    """Annualized GBM parameter estimates from one observed path.

    ``mu_hat`` estimates the **SDE drift** :math:`\\mu`;
    ``log_drift_hat`` estimates :math:`\\mu - \\tfrac{1}{2}\\sigma^2` (the
    drift of the log price); ``sigma_hat`` estimates :math:`\\sigma`.
    All satisfy ``mu_hat == log_drift_hat + sigma_hat**2 / 2`` exactly.
    """

    mu_hat: float
    sigma_hat: float
    log_drift_hat: float


def estimate_gbm(
    log_returns: pd.Series, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> GBMEstimate:
    """Estimate annualized GBM parameters from observed log returns.

    Implements the estimators derived in the module docstring:
    :math:`\\hat\\sigma = s_r\\sqrt{p}`,
    :math:`\\widehat{\\mu - \\sigma^2/2} = \\bar{r}p`,
    :math:`\\hat\\mu = \\bar{r}p + \\hat\\sigma^2/2`.
    All fields are NaN with fewer than two returns.
    """
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year must be >= 1, got {periods_per_year}")
    if len(log_returns) < 2:
        nan = float("nan")
        return GBMEstimate(mu_hat=nan, sigma_hat=nan, log_drift_hat=nan)
    sigma_hat = float(log_returns.std(ddof=1)) * math.sqrt(periods_per_year)
    log_drift_hat = float(log_returns.mean()) * periods_per_year
    return GBMEstimate(
        mu_hat=log_drift_hat + 0.5 * sigma_hat**2,
        sigma_hat=sigma_hat,
        log_drift_hat=log_drift_hat,
    )


# --------------------------------------------------------------------------
# Summary bundles consumed by the GUI
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ReturnStats:
    """Summary statistics for one price series.

    All returns/volatilities are decimal fractions (0.10 = 10%), not
    percentages. ``mean_*``/``std_*``/``geometric_mean_return`` are
    **per-period** quantities; ``cagr``/``annualized_vol``/``sharpe`` are
    annualized with the ``periods_per_year`` passed to ``summary_stats``.
    ``skewness``/``excess_kurtosis`` are computed on log returns.
    """

    n_observations: int
    start_price: float
    end_price: float
    total_return: float
    cagr: float
    mean_simple_return: float
    geometric_mean_return: float
    mean_log_return: float
    std_simple_return: float
    std_log_return: float
    annualized_vol: float
    sharpe: float
    max_drawdown: float
    skewness: float
    excess_kurtosis: float


def summary_stats(
    history: PriceHistory, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> ReturnStats:
    """Compute ``ReturnStats`` for ``history``.

    Requires at least two prices (one return). Quantities whose estimators
    need more data than is present (sample std with one return, moments with
    zero variance) are NaN rather than errors, so short or degenerate series
    still summarize.
    """
    closes = history.closes
    if len(closes) < 2:
        raise ValueError("summary_stats requires at least two prices")
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year must be >= 1, got {periods_per_year}")

    simple = history.simple_returns()
    log_ret = history.log_returns()

    std_simple = float(simple.std(ddof=1)) if len(simple) >= 2 else float("nan")
    std_log = float(log_ret.std(ddof=1)) if len(log_ret) >= 2 else float("nan")

    if std_simple > 0 and np.isfinite(std_simple):
        sharpe = math.sqrt(periods_per_year) * float(simple.mean()) / std_simple
    else:
        sharpe = float("nan")

    return ReturnStats(
        n_observations=len(closes),
        start_price=float(closes.iloc[0]),
        end_price=float(closes.iloc[-1]),
        total_return=total_return(closes),
        cagr=cagr(closes, periods_per_year),
        mean_simple_return=float(simple.mean()),
        geometric_mean_return=geometric_mean_return(closes),
        mean_log_return=float(log_ret.mean()),
        std_simple_return=std_simple,
        std_log_return=std_log,
        annualized_vol=annualized_volatility(log_ret, periods_per_year),
        sharpe=sharpe,
        max_drawdown=max_drawdown(closes),
        skewness=skewness(log_ret),
        excess_kurtosis=excess_kurtosis(log_ret),
    )


@dataclass(frozen=True)
class SeriesAnalysis:
    """Everything the dashboard needs about one price series, in one object.

    Produced off the GUI thread by ``analyze_history``; the GUI only formats
    and plots. One analysis = one consistent state: every chart and label
    rendered from a ``SeriesAnalysis`` describes the same data.
    """

    history: PriceHistory
    simple_returns: pd.Series
    log_returns: pd.Series
    rolling_vol: pd.Series          # annualized, leading NaN window dropped
    drawdowns: pd.Series
    stats: ReturnStats
    gbm: GBMEstimate
    vol_window: int
    periods_per_year: int


def analyze_history(
    history: PriceHistory,
    vol_window: int = DEFAULT_VOL_WINDOW,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> SeriesAnalysis:
    """Run the full analytics pipeline over ``history``."""
    log_ret = history.log_returns()
    return SeriesAnalysis(
        history=history,
        simple_returns=history.simple_returns(),
        log_returns=log_ret,
        rolling_vol=rolling_volatility(log_ret, vol_window, periods_per_year),
        drawdowns=drawdown_series(history.closes),
        stats=summary_stats(history, periods_per_year),
        gbm=estimate_gbm(log_ret, periods_per_year),
        vol_window=vol_window,
        periods_per_year=periods_per_year,
    )
