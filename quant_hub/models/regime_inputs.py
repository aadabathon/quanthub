r"""The regime model's inputs, each as it was known at the time (REGIME_MODEL Q1).

REGIME_MODEL_PLAN's model reads inputs on several clocks: volatility,
turbulence and trend daily; valuation and the stock–bond correlation
monthly. Its evidence run (Q3) fits forecasts walk-forward on a century of
data, so every input must be what an investor could have read on the day,
not what the full sample says now. This module builds each input that way,
and puts it on a scale that uses only its own past.

1. Known at the close of day t
------------------------------
An input's value on day t uses only data published by the close of t (the
discipline of Goyal & Welch 2008). Two rules make that hold:

- **Daily inputs** use returns through t.
- **A monthly value** describing month m, with a publication lag of ℓ
  months, is used from the first day of month m + ℓ + 1. Each monthly
  input is indexed by that date, its *known-from* date. Using it from the
  1st rather than from the month's last close is conservative by a day.

The lags of Shiller's series, in months:

====================  ===  =================================================
series                 ℓ   why
====================  ===  =================================================
P (average of closes)  0   known when the month ends
GS10 (average yield)   0   likewise
CPI                    1   published in the middle of the next month
E and D (trailing      4   the owner's file of 2026-10-24 held earnings
12 months)                 through June
====================  ===  =================================================

2. Valuation: CAPE and the excess CAPE yield
--------------------------------------------
On the first day of month N, the latest months known are P_{N−1},
GS10_{N−1}, CPI_{N−1−ℓ_C} and E_{N−1−ℓ_E}. With the 120 months of real
earnings ending at E's latest month (Campbell & Shiller 1988's smoothing):

    CAPE_N = (P_{N−1} / CPI_{N−1−ℓ_C}) / ( (1/120) Σ_{j=0}^{119} E_{k−j} / CPI_{k−j} ),
             k = N − 1 − ℓ_E

The CPI's base cancels between the two ratios. The latest price is
deflated by the latest CPI known, which is a month older than the price.
With every lag 0, CAPE_N is Shiller's own formula for month N − 1, which is
the test.

The excess CAPE yield sets the earnings yield against a real bond yield,
with the bond's real yield taken as its nominal yield less the trailing
10-year inflation:

    π̄_N   = (CPI_c / CPI_{c−120})^{1/10} − 1,          c = N − 1 − ℓ_C
    ECY_N = 1/CAPE_N − (GS10_{N−1}/100 − π̄_N)

3. The stock–bond correlation (monthly)
---------------------------------------
- **The stock's return** in month m, with the dividend lagged like
  earnings: R^s_m = (P_m + D_{m−ℓ_D}/12) / P_{m−1} − 1.
- **The bond's return:** a 10-year par bond bought at the end of month
  m − 1, so its annual coupon is c = y_{m−1} (GS10 as a fraction). A month
  later it has T = 10 − 1/12 years left, priced at y_m with annual
  compounding:

      P(y) = c (1 − (1+y)^{−T}) / y + (1+y)^{−T},     R^b_m = P(y_m) − 1 + c/12

  If the yield doesn't move, P = 1 and the return is the month's coupon,
  c/12. For a small move, R^b ≈ c/12 − D·Δy, with the par bond's modified
  duration D = (1 − (1+y)^{−T}) / y.
- **The correlation** is Pearson's over the W = 36 months ending at m, and
  is known from m + 1.

Shiller's yields are monthly averages, so these returns are smoothed a
little. Daily bond returns would need FRED's DGS10 or IBKR's bonds.

4. Trend and realized volatility (daily)
----------------------------------------
On the market's daily total return R_t (Ken French: Mkt-RF + RF) and the
bill RF_t, both simple:

- **The 12-month excess return:** Σ_{s=t−251}^{t} [ln(1 + R_s) − ln(1 + RF_s)].
- **Against the 200-day average:** L_t / mean(L_{t−199..t}) − 1, with L the
  total-return index Π(1 + R). French has no price index, so the index
  includes dividends.
- **Realized volatility:** √( (252/21) Σ_{last 21} ln(1 + R_s)² ). It's
  zero-mean, as variance swaps settle and as D76's realized variance is.

5. GARCH and the HMM, walk-forward
----------------------------------
Both models are fitted to log returns r_t = ln(1 + R_t). One fit on the
whole sample would give every past day parameters estimated from later
data. W7's calm gate does that for its history (D49): it's fine for today's
value, where the sample ends, but not for a history used as an input.

**Here, the models are refitted once a year.**
- **When:** on the first trading day τ of each calendar year.
- **On what:** the returns dated in [τ − L years, τ). L is 10 years by
  default, never anything on τ or after.
- **Before the next refit,** those parameters run forward from the
  window's start:
  - **GARCH:** the variance recursion, and after day t's return, h_{t+1}.
    The h-day average ahead is W7's closed form,
    σ̂²_{t,h} = σ̄² + (h_{t+1} − σ̄²)(1 − φʰ) / (h(1 − φ)), annualized as
    √(252 σ̂²) (Bollerslev 1986).
  - **The HMM:** the forward filter, with π_t = α_t(calm), the
    probability of the regime with the lowest σ (Hamilton 1989).
- **Nothing later moves an earlier value.** Refits are anchored to
  calendar years, so appending data never moves one. Both recursions are
  causal.
- **Before the first window fills,** the values are missing. A window is
  full when the data start within its first week, since a weekend or a
  holiday may come first. A year whose fit fails is missing too, and
  named.
- **The cost:** on French's market, 1926–2026, the 90 refits took 218 s,
  about 2.4 s a year for both models. Expanding windows would take several
  times that, since a fit's cost grows with its data.

6. The real-time scales
-----------------------
For an input x on its own grid (days or months), with n_t finite values
before t:

    p_t = ( #{s < t : x_s < x_t} + ½ #{s < t : x_s = x_t} ) / n_t
    z_t = (x_t − x̄_{<t}) / s_{<t}          (s with n − 1)

- **When they start:** both are missing until n_t reaches a minimum: 120
  months, or 1,260 days.
- **Where they're computed:** on the input's own grid, before a monthly
  input is carried onto days, so a month counts once.
- **Ties count half,** as in ``variance_premium.percentile``.
- **Nothing later changes them:** both read only x_{≤t}, so appending
  values never changes an earlier p or z.

API
---
- ``expanding_percentile`` and ``expanding_z``: section 6.
- ``known_from(series, lag)``: a monthly series indexed by its known-from
  date.
- ``real_time_cape`` and ``excess_cape_yield``: section 2.
- ``bond_return``, ``stock_bond_correlation``: section 3.
- ``trend_excess``, ``trend_average``, ``realized_vol``: section 4.
- ``walk_forward``: section 5.
- ``RegimeInput``: one input, raw and on both scales. ``on_days`` carries
  one onto a daily grid.

Tests, against closed forms and planted truths
----------------------------------------------
- **The scales:** they match a direct count and the mean and sd of earlier
  values; they're missing until the minimum; ties count half.
- **No look-ahead:** truncating every series at a date changes no value
  before it, for every input, the walk-forward included.
- **The lags:** a monthly value appears exactly on the 1st of m + ℓ + 1.
- **CAPE** with lags of 0 equals Shiller's formula, and ECY its definition.
- **The bond:** an unmoved yield returns c/12, and a small move matches
  the duration approximation.
- **The correlation** equals Pearson's on each window, and recovers a
  planted ρ within its Fisher interval.
- **The walk-forward's parameters** come only from the window before each
  refit, and its first year is missing.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right, insort
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_hub.models.garch import conditional_variances, fit_garch11
from quant_hub.models.markov import fit_hmm, hmm_filter

TRADING_DAYS = 252
CAPE_MONTHS = 120
INFLATION_MONTHS = 120
LAGS = {"price": 0, "gs10": 0, "cpi": 1, "earnings": 4, "dividend": 4}
"""Publication lags in months (section 1)."""

BOND_YEARS = 10.0
CORRELATION_MONTHS = 36
TREND_DAYS = 252
AVERAGE_DAYS = 200
REALIZED_DAYS = 21
GARCH_HORIZON = 21
"""Days ahead for GARCH's average (REGIME_MODEL decision 3 can change it)."""
REFIT_YEARS = 10
REGIMES = 2
MIN_MONTHS = 120
MIN_DAYS = 1260
"""Earlier values a percentile or z needs (section 6)."""


@dataclass(frozen=True)
class RegimeInput:
    """One input: its raw value indexed by the date it's first known, and
    its real-time percentile and z on the same index."""

    name: str
    title: str
    frequency: str        # "daily" or "monthly"
    lag: str              # what it waits for, in words
    raw: pd.Series
    percentile: pd.Series
    z: pd.Series

    @classmethod
    def build(cls, name: str, title: str, frequency: str, lag: str, raw: pd.Series,
              min_history: int) -> RegimeInput:
        raw = raw.dropna().astype(float)
        values = raw.to_numpy()
        return cls(name, title, frequency, lag, raw,
                   pd.Series(expanding_percentile(values, min_history), raw.index),
                   pd.Series(expanding_z(values, min_history), raw.index))

    def on_days(self, days: pd.DatetimeIndex) -> pd.DataFrame:
        """Raw, percentile and z on each day: the latest value known by then."""
        frame = pd.DataFrame({"raw": self.raw, "percentile": self.percentile, "z": self.z})
        return frame.reindex(frame.index.union(days)).ffill().reindex(days)


# ------------------------------------------------------------------ scales

def expanding_percentile(values, min_history: int) -> np.ndarray:
    """Each value's percentile among the finite values before it (section 6)."""
    _check_minimum(min_history)
    x = np.asarray(values, dtype=float)
    out = np.full(len(x), np.nan)
    seen: list[float] = []
    for i, v in enumerate(x):
        if not math.isfinite(v):
            continue
        n = len(seen)
        if n >= min_history:
            below, through = bisect_left(seen, v), bisect_right(seen, v)
            out[i] = (below + 0.5 * (through - below)) / n
        insort(seen, v)
    return out


def expanding_z(values, min_history: int) -> np.ndarray:
    """Each value less the mean of the finite values before it, over their
    sample standard deviation (section 6)."""
    _check_minimum(min_history)
    x = np.asarray(values, dtype=float)
    finite = np.isfinite(x)
    v = np.where(finite, x, 0.0)
    n = np.cumsum(finite) - finite
    total = np.cumsum(v) - v
    squares = np.cumsum(v * v) - v * v
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = total / n
        sd = np.sqrt(np.maximum(squares - n * mean**2, 0.0) / (n - 1))
        z = (x - mean) / sd
    return np.where(finite & (n >= max(min_history, 2)) & (sd > 0), z, np.nan)


def _check_minimum(min_history: int) -> None:
    if min_history < 2:
        raise ValueError("a scale needs at least two earlier values")


# ----------------------------------------------------------------- monthly

def monthly(series: pd.Series) -> pd.Series:
    """A monthly series on every month from its first to its last, dated the
    1st; a month it lacks is missing, never filled."""
    if not isinstance(series.index, pd.DatetimeIndex) or not len(series):
        raise ValueError("a monthly series needs dated values")
    if (series.index.day != 1).any():
        raise ValueError("monthly values are dated the 1st")
    months = pd.date_range(series.index[0], series.index[-1], freq="MS")
    return series.astype(float).reindex(months)


def known_from(series: pd.Series, lag: int) -> pd.Series:
    """Month m's value, indexed by the 1st of month m + lag + 1 (section 1)."""
    if lag < 0:
        raise ValueError("a lag can't be negative")
    shifted = series.copy()
    shifted.index = shifted.index + pd.DateOffset(months=lag + 1)
    return shifted


def real_time_cape(price: pd.Series, earnings: pd.Series, cpi: pd.Series,
                   lags: dict[str, int] = LAGS) -> pd.Series:
    """CAPE as known on the 1st of each month (section 2)."""
    price, earnings, cpi = monthly(price), monthly(earnings), monthly(cpi)
    real_earnings = (earnings / cpi.reindex(earnings.index)).rolling(
        CAPE_MONTHS, min_periods=CAPE_MONTHS).mean()
    real_price = known_from(price, lags["price"]) / known_from(cpi, lags["cpi"])
    return (real_price / known_from(real_earnings, lags["earnings"])).dropna()


def excess_cape_yield(price: pd.Series, earnings: pd.Series, cpi: pd.Series,
                      gs10: pd.Series, lags: dict[str, int] = LAGS) -> pd.Series:
    """1/CAPE less the real 10-year yield, as known on the 1st of each month
    (section 2)."""
    cape = real_time_cape(price, earnings, cpi, lags)
    cpi = monthly(cpi)
    inflation = (cpi / cpi.shift(INFLATION_MONTHS)) ** (12 / INFLATION_MONTHS) - 1
    real_yield = known_from(monthly(gs10), lags["gs10"]) / 100 - known_from(inflation,
                                                                            lags["cpi"])
    return (1 / cape - real_yield).dropna()


def bond_return(previous_yield, yield_now, years: float = BOND_YEARS) -> np.ndarray:
    """A month's return on a par bond bought at ``previous_yield`` with
    ``years`` to run, priced at ``yield_now`` (section 3). Yields are
    fractions."""
    c = np.asarray(previous_yield, dtype=float)
    y = np.asarray(yield_now, dtype=float)
    if np.any(y <= 0) or np.any(c <= 0):
        raise ValueError("yields must be positive")
    remaining = years - 1 / 12
    discount = (1 + y) ** -remaining
    return c * (1 - discount) / y + discount - 1 + c / 12


def stock_bond_correlation(price: pd.Series, dividend: pd.Series, gs10: pd.Series,
                           window: int = CORRELATION_MONTHS,
                           lags: dict[str, int] = LAGS) -> pd.Series:
    """Pearson's correlation of the stock's and the bond's monthly returns
    over ``window`` months, as known on the 1st of the next month (section 3)."""
    if window < 3:
        raise ValueError("a correlation needs a window of at least three months")
    price, dividend, gs10 = monthly(price), monthly(dividend), monthly(gs10)
    lagged_dividend = dividend.shift(lags["dividend"]).reindex(price.index)
    stock = (price + lagged_dividend / 12) / price.shift(1) - 1
    y = gs10 / 100
    bond = pd.Series(np.nan, index=y.index)
    both = y.notna() & y.shift(1).notna()
    bond[both] = bond_return(y.shift(1)[both], y[both])
    pair = pd.DataFrame({"stock": stock, "bond": bond})
    rho = pair["stock"].rolling(window, min_periods=window).corr(pair["bond"])
    return known_from(rho, max(lags["price"], lags["gs10"])).dropna()


# ------------------------------------------------------------------- daily

def trend_excess(total: pd.Series, bills: pd.Series, days: int = TREND_DAYS) -> pd.Series:
    """The log excess return over the last ``days`` days (section 4)."""
    excess = np.log1p(total) - np.log1p(bills.reindex(total.index))
    return excess.rolling(days, min_periods=days).sum().dropna()


def trend_average(total: pd.Series, days: int = AVERAGE_DAYS) -> pd.Series:
    """The total-return index against its ``days``-day average, less one."""
    level = np.exp(np.log1p(total).cumsum())
    return (level / level.rolling(days, min_periods=days).mean() - 1).dropna()


def realized_vol(total: pd.Series, days: int = REALIZED_DAYS) -> pd.Series:
    """Zero-mean annualized volatility over the last ``days`` days."""
    squares = np.log1p(total) ** 2
    return np.sqrt(TRADING_DAYS * squares.rolling(days, min_periods=days).mean()).dropna()


@dataclass(frozen=True)
class Refit:
    """One year's fit: the day it takes effect and the window it saw."""

    day: pd.Timestamp
    window_start: pd.Timestamp
    observations: int
    problem: str = ""     # why a model couldn't be fitted, if one couldn't


@dataclass(frozen=True)
class WalkForward:
    garch_vol: pd.Series     # annualized √(252 σ̂²_{t,h}), known at the close of t
    calm: pd.Series          # α_t(calm)
    refits: tuple[Refit, ...]
    horizon: int
    regimes: int
    years: int


def walk_forward(total: pd.Series, years: int = REFIT_YEARS, horizon: int = GARCH_HORIZON,
                 regimes: int = REGIMES) -> WalkForward:
    """GARCH's expected volatility and the HMM's calm probability, refitted on
    the first trading day of each year on the ``years`` before it (section 5)."""
    if years < 1 or horizon < 1 or regimes < 2:
        raise ValueError("need a window of a year or more, a horizon of a day or more, "
                         "and two regimes or more")
    r = np.log1p(total.astype(float)).dropna()
    days = r.index
    values = r.to_numpy()
    starts = [days[i] for i in range(len(days)) if i == 0 or days[i].year != days[i - 1].year]
    # A window is full when the data start within its first week (a weekend
    # or a holiday may come first).
    refit_days = [d for d in starts
                  if d - pd.DateOffset(years=years) + pd.Timedelta(days=7) >= days[0]]
    vol = pd.Series(np.nan, index=days)
    calm = pd.Series(np.nan, index=days)
    refits = []
    for i, day in enumerate(refit_days):
        start = day - pd.DateOffset(years=years)
        end = refit_days[i + 1] if i + 1 < len(refit_days) else None
        window = (days >= start) & (days < day)
        span = (days >= start) & ((days < end) if end is not None else True)
        after = span & (days >= day)
        problems = []
        try:
            garch = fit_garch11(values[window]).parameters
        except ValueError as exc:
            problems.append(f"GARCH: {exc}")
        else:
            run = values[span]
            h = conditional_variances(run, garch)
            following = garch.omega + garch.alpha * (run - garch.mean) ** 2 + garch.beta * h
            p, long_run = garch.persistence, garch.unconditional_variance
            average = long_run + (following - long_run) * (1 - p**horizon) / (horizon * (1 - p))
            vol[after] = np.sqrt(TRADING_DAYS * average)[days[span] >= day]
        try:
            model = fit_hmm(values[window], regimes).model
        except ValueError as exc:
            problems.append(f"HMM: {exc}")
        else:
            filtered = hmm_filter(model, values[span]).filtered[:, int(np.argmin(model.sigmas))]
            calm[after] = filtered[days[span] >= day]
        refits.append(Refit(day, days[window][0], int(window.sum()), "; ".join(problems)))
    return WalkForward(vol.dropna(), calm.dropna(), tuple(refits), horizon, regimes, years)
