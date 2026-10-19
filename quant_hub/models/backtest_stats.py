r"""Performance and Sharpe inference for daily research (R1).

All inputs are realized **net simple returns**, not log returns or price
levels. Annualization uses p observed sessions/year, default 252. Cash has
effective annual rate r, so daily excess returns are x=R-(1+r)^(1/p)+1.
No risk-free rate is estimated from the strategy's sample.

Performance
-----------
For n periods, wealth starts at one before the first return and is
V_t=prod_(j<=t)(1+R_j). CAGR=exp(p/n * sum log(1+R))-1; annual volatility
is sample sd(R)*sqrt(p). Drawdown includes that initial one, so a loss on
the very first day counts. Sortino uses the full-sample lower partial
second moment: sqrt(p)*mean(x)/sqrt(mean(min(x,0)^2)), not the conditional
sd of losing days. Ratios with zero denominators are NaN, never infinity.
Turnover is total absolute notional / pre-trade equity (both buys and
sells); its annualized value is sum(turnover)*p/n.

Sharpe standard error, derived by the delta method
------------------------------------------------
Write the per-period Sharpe as s=mu/sigma. A perturbation gives
ds=dmu/sigma - mu/(2 sigma^3) d(sigma^2). With standardized z=(x-mu)/sigma,
the influence is psi=z - s/2*(z^2-1). For independent observations,

    Var(psi) = 1 - gamma_3*s + (gamma_4-1)*s^2/4,
    SE(s_hat) ~= sqrt(Var(psi)/(n-1)).

gamma_3 and gamma_4 are plain standardized central moments (kurtosis is
NOT excess kurtosis). Gaussian returns give 1+s^2/2. Both s and its SE
are multiplied by sqrt(p) for reporting. The n-1 denominator follows the
usual sample correction. These are asymptotic IID errors, explicitly
named ``standard_error_iid``; autocorrelated returns require other
inference. A backtest's position lag alone does not justify independence.

Probabilistic and deflated Sharpe
--------------------------------
The normal approximation gives PSR(s0)=Phi((s_hat-s0)/SE(s_hat)). It is an
asymptotic significance diagnostic, not a posterior probability or a
guarantee about future profitability. For selection among N independent
trials with null mean Sharpe zero and cross-trial sd tau, the expected
maximum is approximated by

    s0 = tau * [(1-gamma)*Phi^-1(1-1/N)
                + gamma*Phi^-1(1-1/(N*e))],

where gamma is Euler's constant. N=1 gives s0=0 exactly. DSR is PSR at
this threshold. We estimate tau from ALL supplied trial Sharpes, including
the unselected ones. The caller must explicitly supply the effective
independent trial count; correlated parameter neighbours do not magically
become independent trials. All trial Sharpes and thresholds use the same
annualization and sample convention. Unknown/missing trial history cannot
be reconstructed by this module. The extreme-value approximation is most
accurate for larger N; small-N values are approximate.

Source for the selection threshold and PSR convention: Bailey & Lopez de
Prado (2014), The Deflated Sharpe Ratio, equations (1)-(2):
https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf

Validation: hand-computed wealth/Sortino, Gaussian SE and empirical
coverage, simulated normal maxima, invariance to annualization, and the
best of many independent null trials (selection raises PSR; DSR deflates it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Integral
from statistics import NormalDist

import numpy as np
import pandas as pd

from quant_hub.data.stats import TRADING_DAYS_PER_YEAR

_NORMAL = NormalDist()
_EULER_GAMMA = 0.5772156649015329


@dataclass(frozen=True)
class SharpeEstimate:
    sharpe: float                   # annualized
    standard_error_iid: float       # annualized, independent-observation assumption
    skewness: float
    kurtosis: float                 # normal = 3, not excess
    observations: int

    def probability_above(self, benchmark_sharpe: float = 0.0) -> float:
        """PSR above an annualized benchmark; NaN for undefined inference."""
        if not math.isfinite(benchmark_sharpe):
            raise ValueError("benchmark_sharpe must be finite")
        if not math.isfinite(self.standard_error_iid) or self.standard_error_iid <= 0:
            return float("nan")
        return _NORMAL.cdf((self.sharpe - benchmark_sharpe) / self.standard_error_iid)


@dataclass(frozen=True)
class PerformanceSummary:
    observations: int
    total_return: float
    cagr: float
    volatility: float
    sharpe: SharpeEstimate
    sortino: float
    max_drawdown: float
    turnover_total: float | None
    turnover_annual: float | None


@dataclass(frozen=True)
class DeflatedSharpe:
    probability: float
    benchmark_sharpe: float          # annualized expected maximum under the null
    estimate: SharpeEstimate
    independent_trials: int
    recorded_trials: int


def sharpe_estimate(returns: pd.Series, *, cash_rate: float = 0.0,
                    periods_per_year: int = TRADING_DAYS_PER_YEAR) -> SharpeEstimate:
    """Ex-post Sharpe and the module's IID standard error (NaN if undefined)."""
    values = _returns(returns)
    cash = _cash_return(cash_rate, periods_per_year)
    n = len(values)
    if n < 2:
        return SharpeEstimate(*(float("nan"),) * 4, observations=n)
    x = values - cash
    centered = x - x.mean()
    m2 = float(np.mean(centered**2))
    if np.ptp(x) == 0 or m2 == 0:
        return SharpeEstimate(*(float("nan"),) * 4, observations=n)
    z = centered / math.sqrt(m2)
    skew, kurt = float(np.mean(z**3)), float(np.mean(z**4))
    daily = float(x.mean()) / math.sqrt(m2 * n / (n - 1))
    variance = 1.0 - skew * daily + (kurt - 1.0) * daily**2 / 4
    se = math.sqrt(max(variance, 0.0) / (n - 1)) if n >= 3 else float("nan")
    scale = math.sqrt(periods_per_year)
    return SharpeEstimate(daily * scale, se * scale, skew, kurt, n)


def rolling_sharpe(returns: pd.Series, window: int = 63, *, cash_rate: float = 0.0,
                   periods_per_year: int = TRADING_DAYS_PER_YEAR) -> pd.Series:
    """Trailing sqrt(periods/year) * mean(excess) / sample sd(excess).

    Requires a complete window. Constant-return windows and warm-up are
    NaN; there is no infinite Sharpe or implicit fill. This is descriptive,
    without an independence correction or a confidence interval.
    """
    _returns(returns)
    if isinstance(window, bool) or not isinstance(window, Integral) or window < 2:
        raise ValueError("rolling window must be an integer >= 2")
    excess = returns - _cash_return(cash_rate, periods_per_year)
    sample = excess.rolling(window)
    sd = sample.std(ddof=1).where(sample.max() > sample.min()).replace(0, np.nan)
    return sample.mean() / sd * math.sqrt(periods_per_year)


def performance_summary(returns: pd.Series, *, cash_rate: float = 0.0,
                        periods_per_year: int = TRADING_DAYS_PER_YEAR,
                        turnover: pd.Series | None = None) -> PerformanceSummary:
    """Summarize the realized window; no implicit dropping of missing returns.

    ``turnover`` is optional and must contain one nonnegative observation
    per return, in period order. Its timestamps are decision dates, hence
    intentionally one session earlier than the returns from ``backtest``.
    """
    values = _returns(returns)
    cash = _cash_return(cash_rate, periods_per_year)
    n = len(values)
    wealth = np.r_[1.0, np.cumprod(1.0 + values)]
    if not np.isfinite(wealth).all() or (wealth <= 0).any():
        raise ValueError("wealth overflow or underflow in performance window")
    downside = math.sqrt(float(np.mean(np.minimum(values - cash, 0.0)**2)))
    sortino = float("nan")
    if downside > 0:
        sortino = math.sqrt(periods_per_year) * float(np.mean(values - cash)) / downside
    volatility = float("nan")
    if n >= 2:
        volatility = (0.0 if np.ptp(values) == 0
                      else float(np.std(values, ddof=1)) * math.sqrt(periods_per_year))
    total_turnover = annual_turnover = None
    if turnover is not None:
        if not isinstance(turnover, pd.Series) or len(turnover) != n:
            raise ValueError("turnover needs one observation per return, in period order")
        traded = turnover.to_numpy(dtype=float)
        if not np.isfinite(traded).all() or (traded < 0).any():
            raise ValueError("turnover must be finite and nonnegative")
        total_turnover = float(traded.sum())
        annual_turnover = total_turnover * periods_per_year / n
    return PerformanceSummary(
        observations=n, total_return=float(wealth[-1] - 1),
        cagr=math.expm1(float(np.log1p(values).mean()) * periods_per_year),
        volatility=volatility,
        sharpe=sharpe_estimate(returns, cash_rate=cash_rate, periods_per_year=periods_per_year),
        sortino=sortino, max_drawdown=float(np.min(wealth / np.maximum.accumulate(wealth) - 1)),
        turnover_total=total_turnover, turnover_annual=annual_turnover,
    )


def deflated_sharpe(returns: pd.Series, trial_sharpes: np.ndarray,
                    independent_trials: int, *, cash_rate: float = 0.0,
                    periods_per_year: int = TRADING_DAYS_PER_YEAR) -> DeflatedSharpe:
    """DSR using annualized Sharpes of all recorded trials and explicit effective N.

    The cross-trial variance is the sample variance. One effective trial
    gives ordinary PSR against zero and does not need a variance estimate.
    """
    trials = np.asarray(trial_sharpes, dtype=float)
    if trials.ndim != 1 or len(trials) < 1 or not np.isfinite(trials).all():
        raise ValueError("trial_sharpes must be a nonempty, finite vector")
    if (isinstance(independent_trials, bool) or not isinstance(independent_trials, Integral)
            or not 1 <= independent_trials <= len(trials)):
        raise ValueError("independent_trials must be an integer between one and recorded trial count")
    estimate = sharpe_estimate(returns, cash_rate=cash_rate, periods_per_year=periods_per_year)
    threshold = 0.0
    if independent_trials > 1:
        spread = float(np.std(trials, ddof=1))
        if spread == 0:
            raise ValueError("multiple independent trials require a nonzero cross-trial variance")
        # Symmetry uses lower-tail probabilities, avoiding 1-small rounding.
        threshold = spread * ((1 - _EULER_GAMMA) * -_NORMAL.inv_cdf(1 / independent_trials)
                              + _EULER_GAMMA * -_NORMAL.inv_cdf(1 / independent_trials / math.e))
    return DeflatedSharpe(estimate.probability_above(threshold), threshold, estimate,
                          int(independent_trials), len(trials))


def _cash_return(rate: float, periods: int) -> float:
    if not math.isfinite(rate) or rate <= -1:
        raise ValueError("cash_rate must be finite and exceed -1")
    if isinstance(periods, bool) or not isinstance(periods, Integral) or periods < 1:
        raise ValueError("periods_per_year must be a positive integer")
    return math.expm1(math.log1p(rate) / periods)


def _returns(returns: pd.Series) -> np.ndarray:
    if not isinstance(returns, pd.Series) or len(returns) == 0:
        raise ValueError("returns must be a nonempty Series")
    if returns.index.has_duplicates or not returns.index.is_monotonic_increasing:
        raise ValueError("returns must be in unique chronological order")
    values = returns.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= -1).any():
        raise ValueError("returns must be finite and exceed -1")
    return values
