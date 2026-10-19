r"""Portfolio risk: what today's holdings could lose, and why (P1).

Built for a stock and ETF portfolio that uses options sparingly: risk
first, every number traceable to the holdings. The derivation below came
before the code.

Exposures
---------
The account's equity :math:`E` is every position's market value plus cash.
For risk, each holding is a dollar exposure :math:`X_i` to one underlying:

* a stock or ETF: its market value :math:`q_i P_i`;
* an option: its delta-equivalent :math:`c\,m\,\Delta\,S` (contracts
  :math:`c`, signed; multiplier :math:`m = 100`; :math:`\Delta` from
  Black–Scholes at the underlying's realized vol, the page's rate, no
  dividend yield), added to its underlying's exposure;
* cash: none.

Weights :math:`w_i = X_i / E` are fractions of equity, so leverage, shorts
and cash all show: :math:`\sum_i w_i` is the net exposure, 0.95 for an
account 5% in cash.

Returns
-------
Daily simple returns :math:`R_{t,i} = P_t/P_{t-1} - 1` of adjusted closes
(total return, D38) over the lookback, on the days every holding (and the
benchmark) traded. Simple rather than log returns, because dollar P&L is
linear in them: on day :math:`t`, today's holdings would have made
:math:`\mathrm{P\&L}_t = \sum_i X_i R_{t,i}`, a return of
:math:`R_{p,t} = \sum_i w_i R_{t,i}` on equity.

Volatility and risk contributions
---------------------------------
With :math:`\Sigma` the sample covariance of daily returns
(:math:`n - 1` denominator), the portfolio's daily volatility, as a fraction
of equity, is

.. math::  \sigma_p = \sqrt{w^\top \Sigma w},

exactly the sample standard deviation of :math:`R_{p,t}`. Annualized
:math:`\times\sqrt{252}`.

:math:`\sigma_p` is homogeneous of degree one in :math:`w` (doubling every
position doubles the risk), so Euler's theorem splits it exactly:

.. math::

    \sigma_p = \sum_i w_i \frac{\partial \sigma_p}{\partial w_i}
             = \sum_i \frac{w_i (\Sigma w)_i}{\sigma_p} \equiv \sum_i RC_i .

:math:`RC_i/\sigma_p` is holding :math:`i`'s share of the risk; the shares
sum to 100%. A holding that diversifies the rest has a small or negative
share, whatever its weight, and a holding with a larger share of risk than
of value is where the risk is concentrated. The page leads with that
comparison.

Beta
----
:math:`\beta_i = \mathrm{Cov}(R_i, R_m)/\mathrm{Var}(R_m)` against a
benchmark :math:`m` (SPY by default), and by linearity
:math:`\beta_p = \sum_i w_i \beta_i = \mathrm{Cov}(R_p, R_m)/\mathrm{Var}(R_m)`.
A market move of :math:`x` costs about :math:`\beta_p\,x\,E`.

Value at risk and expected shortfall
------------------------------------
The one-day loss is :math:`L = -\mathrm{P\&L}`. At confidence :math:`\alpha`
(95% and 99%):

* **Historical:** replay each of the :math:`n` days on today's holdings.
  Stocks move by their returns. Options are repriced in full at the moved
  underlying, one day nearer expiry, vol and rate held, so a put's
  protection counts at its real size rather than its delta's.
  :math:`\mathrm{VaR}_\alpha` is the :math:`\alpha`-quantile of the
  :math:`n` losses (linear interpolation, numpy's default), and
  :math:`\mathrm{ES}_\alpha` the mean of the losses at or beyond it.
* **Gaussian:** :math:`L \sim N(0, \sigma_p^2 E^2)`, drift left out (over a
  day, :math:`\hat\mu` is noise next to :math:`\sigma`), so
  :math:`\mathrm{VaR}_\alpha = z_\alpha \sigma_p E` and
  :math:`\mathrm{ES}_\alpha = \sigma_p E\,\varphi(z_\alpha)/(1-\alpha)`.
  Options enter through their deltas only.

When the two disagree, that's information: fat tails (historical ES well
above Gaussian) or option convexity. A month's Gaussian VaR is the day's
times :math:`\sqrt{21}`, the i.i.d. scaling, stated as an approximation.

Drawdown
--------
Replaying the whole lookback on today's mix (constant weights,
delta-equivalent options) gives a value path
:math:`V_t = \prod_{s \le t}(1 + R_{p,s})` and drawdown
:math:`D_t = V_t/\max_{s \le t} V_s - 1`. Its minimum is what today's
holdings would have suffered over the window: not the account's own
history, which a snapshot can't know.

Concentration
-------------
The effective number of holdings is :math:`N_{\mathrm{eff}} = 1/\sum_i
\hat w_i^2` with :math:`\hat w_i = |X_i|/\sum_j |X_j|`: :math:`n` equal
positions give :math:`n`, and one dominant position gives about 1.

Kelly: how much the estimate can bear
-------------------------------------
Let :math:`x = \sum_i w_i` be the net exposure, and the risky mix have
annual mean :math:`\mu = \mu_p / x` and vol :math:`\sigma = \sigma_p / x`
(annualized). Holding the mix at leverage :math:`f` (continuously
rebalanced, the rest at the rate :math:`r`), the long-run growth rate is

.. math::  g(f) = r + f(\mu - r) - \tfrac12 f^2 \sigma^2,

greatest at :math:`f^* = (\mu - r)/\sigma^2`, where
:math:`g^* = r + \tfrac12 \left((\mu - r)/\sigma\right)^2`. At the current
:math:`f = x`, the drag :math:`\tfrac12 x^2 \sigma^2` is the gap between the
average return and the growth rate.

:math:`\hat\mu` (the sample mean of daily returns, :math:`\times 252`) has
standard error :math:`\sigma/\sqrt{T}` over :math:`T` years, so
:math:`\hat f^*` has standard error :math:`1/(\sigma\sqrt{T})`: for
:math:`\sigma = 15\%` and one year, :math:`\pm 6.7`. The page shows
:math:`\hat f^*` with that error. From a year or two of history, full Kelly
isn't an estimate, it's noise: the case for fractional Kelly, and for not
leaning on :math:`\hat\mu` at all.

Limitations
-----------
* One lookback's covariance: it moves with regimes (the Markov Lab), and is
  noisy when holdings approach the number of days. Ledoit–Wolf shrinkage is
  a v1.1 candidate.
* Options: delta in the covariance, the Gaussian VaR and the drawdown;
  full revaluation only in the one-day historical replay. Vol is held at the
  underlying's realized vol, with no smile.
* Cash earns nothing in the replay.
* Holdings without enough history are left out, and named.

Validation (tests)
------------------
On a correlated synthetic world with known covariance: :math:`\sigma_p`
equals the sample sd of the replayed returns; the contributions sum to
:math:`\sigma_p`; :math:`\beta_p = \sum w_i \beta_i`; the Gaussian VaR and
ES match their closed forms; the historical VaR is exceeded on about
:math:`1-\alpha` of fresh days (binomial tolerance); the estimates recover
the true covariance within sampling error. Options: the replay of a
protective put loses less than its delta says on a crash day.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

from quant_hub.models.black_scholes import greeks_array, price_array

TRADING_DAYS = 252
MONTH_DAYS = 21
CONFIDENCE = (0.95, 0.99)
MIN_DAYS = 60
"""Fewest common return days the model accepts: below about three months
the covariance and the tails are guesses."""

_STANDARD_NORMAL = NormalDist()


@dataclass(frozen=True)
class OptionExposure:
    """An option position, priced with Black–Scholes (module docstring)."""

    underlying: str
    option_type: str
    strike: float
    days: float             # calendar days to expiry
    contracts: float        # signed
    spot: float
    vol: float              # annualized
    rate: float
    multiplier: float = 100.0

    def price(self, spot, days: float) -> np.ndarray:
        return price_array(spot, self.strike, max(days, 1e-6), self.rate, self.vol,
                           self.option_type)

    def delta_dollars(self) -> float:
        """:math:`c\\,m\\,\\Delta\\,S`: the stock exposure the option acts like."""
        delta = float(greeks_array(self.spot, self.strike, self.days, self.rate, self.vol,
                                   self.option_type).delta)
        return self.contracts * self.multiplier * delta * self.spot

    def scenario_pnl(self, returns: np.ndarray) -> np.ndarray:
        """Dollar P&L of a one-day move of the underlying by each of ``returns``,
        repriced in full one day nearer expiry."""
        moved = self.price(self.spot * (1.0 + np.asarray(returns, dtype=float)), self.days - 1)
        now = self.price(self.spot, self.days)
        return self.contracts * self.multiplier * (moved - now)


@dataclass(frozen=True)
class Exposures:
    """Equity, stock and ETF dollars by symbol (signed), and options."""

    equity: float
    stocks: dict[str, float]
    options: tuple[OptionExposure, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.equity) or self.equity <= 0:
            raise ValueError("equity must be positive")

    def by_underlying(self) -> dict[str, float]:
        """Delta-equivalent dollars per underlying (module docstring)."""
        totals = dict(self.stocks)
        for option in self.options:
            totals[option.underlying] = totals.get(option.underlying, 0.0) + option.delta_dollars()
        return totals


@dataclass(frozen=True)
class RiskReport:
    """Everything the module docstring defines, for one portfolio."""

    equity: float
    symbols: tuple[str, ...]
    exposures: np.ndarray            # X_i, dollars (delta-equivalent)
    weights: np.ndarray              # X_i / E
    asset_vols: np.ndarray           # daily
    covariance: np.ndarray           # daily
    vol: float                       # sigma_p, daily, fraction of equity
    contributions: np.ndarray        # RC_i, daily; sums to vol
    betas: np.ndarray | None
    beta: float | None
    pnl: pd.Series                   # the historical replay, dollars per day
    var_historical: dict[float, float]   # dollars
    es_historical: dict[float, float]
    var_gaussian: dict[float, float]
    es_gaussian: dict[float, float]
    path: pd.Series                  # replayed value of today's mix, from 1
    drawdown: pd.Series
    benchmark_path: pd.Series | None
    n_effective: float
    net_exposure: float              # x = sum of weights
    mean: float                      # mu of the mix, annual
    kelly: float                     # f*
    kelly_error: float               # its standard error
    rate: float

    @property
    def vol_annual(self) -> float:
        return self.vol * math.sqrt(TRADING_DAYS)

    @property
    def risk_shares(self) -> np.ndarray:
        """RC_i / sigma_p: each holding's share of the risk, summing to 1."""
        return self.contributions / self.vol

    @property
    def years(self) -> float:
        return len(self.pnl) / TRADING_DAYS

    @property
    def mix_vol(self) -> float:
        """sigma of the risky mix, annual."""
        return self.vol_annual / self.net_exposure

    def growth(self, leverage: float) -> float:
        """g(f), annual (module docstring, Kelly)."""
        sigma = self.mix_vol
        return self.rate + leverage * (self.mean - self.rate) - 0.5 * leverage**2 * sigma**2

    def var_month(self, confidence: float) -> float:
        return self.var_gaussian[confidence] * math.sqrt(MONTH_DAYS)


def scale_to_risk(budget: np.ndarray, covariance: np.ndarray, daily_target: float,
                  gross_cap: float = 1.0) -> np.ndarray:
    r"""Scale a fixed budget without changing its relative allocations.

    Since vol(f b) = |f| sqrt(b' Sigma b), the target requires
    f = daily_target / sqrt(b' Sigma b). The exposure constraint requires
    f <= gross_cap / sum(abs(b)); take the smaller. Zero budget or zero
    estimated variance returns cash. Sigma must be finite symmetric PSD.
    This primitive estimates neither expected returns nor an optimum.
    """
    b, cov = np.asarray(budget, dtype=float), np.asarray(covariance, dtype=float)
    if b.ndim != 1 or cov.shape != (len(b), len(b)) or not np.all(np.isfinite(b)):
        raise ValueError("budget and covariance dimensions must agree and be finite")
    if not np.all(np.isfinite(cov)) or not np.allclose(cov, cov.T, atol=1e-12):
        raise ValueError("covariance must be finite and symmetric")
    if np.linalg.eigvalsh(cov).min(initial=0) < -1e-12:
        raise ValueError("covariance must be positive semidefinite")
    if not np.isfinite(daily_target) or daily_target <= 0 or not np.isfinite(gross_cap) or gross_cap <= 0:
        raise ValueError("risk target and gross cap must be finite and positive")
    variance, gross = float(b @ cov @ b), float(np.abs(b).sum())
    if variance <= 0 or gross == 0:
        return np.zeros_like(b)
    return b * min(daily_target / np.sqrt(variance), gross_cap / gross)


def simple_returns(closes: dict[str, pd.Series]) -> pd.DataFrame:
    """Daily simple returns, on the days every series has a close."""
    frame = pd.DataFrame(closes).dropna(how="any").sort_index()
    return frame.pct_change().iloc[1:]


def risk_report(exposures: Exposures, returns: pd.DataFrame, benchmark: pd.Series | None,
                rate: float, confidence: tuple[float, ...] = CONFIDENCE) -> RiskReport:
    """The module docstring's quantities for ``exposures``.

    ``returns`` holds daily simple returns with a column for every
    underlying (stocks and options' alike); ``benchmark`` the benchmark's,
    on the same days (or None).
    """
    totals = exposures.by_underlying()
    symbols = tuple(sorted(totals, key=lambda s: -abs(totals[s])))
    missing = [s for s in symbols if s not in returns.columns]
    if missing:
        raise ValueError(f"no returns for {missing}")
    if len(returns) < MIN_DAYS:
        raise ValueError(f"{len(returns)} common days of returns; the model needs {MIN_DAYS}")
    if not all(0.5 < c < 1 for c in confidence):
        raise ValueError("confidence levels must be between 0.5 and 1")
    r = returns[list(symbols)].to_numpy(dtype=float)
    if not np.isfinite(r).all():
        raise ValueError("returns must be finite")
    equity = exposures.equity
    x = np.array([totals[s] for s in symbols])
    w = x / equity

    covariance = np.atleast_2d(np.cov(r, rowvar=False))
    portfolio = r @ w
    vol = math.sqrt(max(float(w @ covariance @ w), 0.0))
    contributions = w * (covariance @ w) / vol if vol > 0 else np.zeros_like(w)

    betas = beta = None
    if benchmark is not None:
        m = benchmark.reindex(returns.index).to_numpy(dtype=float)
        if not np.isfinite(m).all():
            raise ValueError("the benchmark needs a return on every day")
        market_var = float(np.var(m, ddof=1))
        betas = np.array([np.cov(r[:, i], m, ddof=1)[0, 1] for i in range(len(symbols))])
        betas = betas / market_var
        beta = float(w @ betas)

    # The historical replay: stocks linear, options repriced in full.
    pnl = r @ np.array([exposures.stocks.get(s, 0.0) for s in symbols])
    for option in exposures.options:
        pnl = pnl + option.scenario_pnl(returns[option.underlying].to_numpy(dtype=float))
    losses = -pnl
    var_h, es_h, var_g, es_g = {}, {}, {}, {}
    for alpha in confidence:
        var_h[alpha] = float(np.quantile(losses, alpha))
        es_h[alpha] = float(losses[losses >= var_h[alpha]].mean())
        z = _STANDARD_NORMAL.inv_cdf(alpha)
        var_g[alpha] = z * vol * equity
        es_g[alpha] = vol * equity * _STANDARD_NORMAL.pdf(z) / (1 - alpha)

    path = pd.Series(np.cumprod(1.0 + portfolio), index=returns.index)
    drawdown = path / path.cummax() - 1.0
    benchmark_path = (None if benchmark is None else
                      pd.Series(np.cumprod(1.0 + benchmark.reindex(returns.index).to_numpy()),
                                index=returns.index))

    gross = np.abs(x).sum()
    n_effective = 1.0 / float(np.sum((np.abs(x) / gross) ** 2)) if gross > 0 else 0.0
    net = float(w.sum())
    mean = kelly = kelly_error = math.nan
    if net > 1e-9 and vol > 0:
        mix_mean = float(portfolio.mean()) * TRADING_DAYS / net
        mix_vol = vol * math.sqrt(TRADING_DAYS) / net
        years = len(portfolio) / TRADING_DAYS
        mean = mix_mean
        kelly = (mix_mean - rate) / mix_vol**2
        kelly_error = 1.0 / (mix_vol * math.sqrt(years))
    return RiskReport(
        equity=equity, symbols=symbols, exposures=x, weights=w,
        asset_vols=np.sqrt(np.diag(covariance)), covariance=covariance, vol=vol,
        contributions=contributions, betas=betas, beta=beta,
        pnl=pd.Series(pnl, index=returns.index), var_historical=var_h, es_historical=es_h,
        var_gaussian=var_g, es_gaussian=es_g, path=path, drawdown=drawdown,
        benchmark_path=benchmark_path, n_effective=n_effective, net_exposure=net,
        mean=mean, kelly=kelly, kelly_error=kelly_error, rate=rate)


def correlated_returns(vols: np.ndarray, correlation: np.ndarray, days: int, mean: np.ndarray,
                       seed: int | None = None) -> np.ndarray:
    """Synthetic daily returns with known covariance: ``days`` draws of a
    multivariate normal, means ``mean`` and covariance D C D (D the vols),
    the world the tests know the answers in."""
    vols, mean = np.asarray(vols, dtype=float), np.asarray(mean, dtype=float)
    correlation = np.asarray(correlation, dtype=float)
    covariance = np.outer(vols, vols) * correlation
    factor = np.linalg.cholesky(covariance)
    rng = np.random.default_rng(seed)
    return mean + rng.standard_normal((days, len(vols))) @ factor.T
