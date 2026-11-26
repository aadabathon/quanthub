r"""The tail voice: the century, scaled by your β (D2c).

The derivation below was reviewed and approved before the code
(2026-11-25, D102–D103). The points that were reviewed are marked
**Review:**; each was kept as proposed.

The question
------------
Every voice so far learns from two years of the book's own prices. Two
years can be calm, and a crash the book has never lived through is
invisible to them. This voice asks a different question: **if the next
year is one of the market's last hundred, drawn at random, what happens to
your book?**

It differs from the baseline in one assumption: **the market's next h
sessions are a stretch of its own history since 1926**, with its drift,
crashes, fat tails and clustering, and the book follows the market through
its β.

1. The book's link to the market
--------------------------------
On the snapshot's sessions (about two years), let :math:`R_{B,t}` be the
book's daily simple return (today's weights replayed, as the baseline does)
and :math:`R_{M,t}` the market's (SPY). Ordinary least squares on

.. math::  R_{B,t} = a + b\,R_{M,t} + \varepsilon_t

gives :math:`\hat b = \widehat{\mathrm{Cov}}(R_B, R_M) / \widehat{\mathrm{Var}}(R_M)`
and the residual variance :math:`s^2 = \sum \hat\varepsilon_t^2 / (n - 2)`.
:math:`\hat b` is the book's β, Sharpe's (1964) single index; :math:`s^2`
is the part of its daily variance the market doesn't explain, the book's
own risk.

- **The intercept is dropped.** Two years of a book's alpha is noise. With
  s near 0.8% a day, its standard error is about
  :math:`252\,s/\sqrt n \approx 9\%` a year. So the voice claims no alpha.
- **Review:** :math:`\hat b`'s own error,
  :math:`s / (\hat\sigma_M \sqrt n)`, about 0.04 for two years of a
  typical book, is reported in the parameters, not carried into the paths.

2. The century
--------------
Ken French's daily files give the market's excess return :math:`x_s`
(Mkt−RF: every NYSE, AMEX and NASDAQ stock, value-weighted) and the
one-month bill :math:`r_s`, from July 1926, as fractions (D60). The daily
run refreshes them when a week old. The library rebuilds them monthly, so
the last month or two is missing, which doesn't matter here: the voice
uses the history's shape, not its last day.

**Review:** French's market is the whole US market; SPY is the S&P 500.
Their daily returns correlate above 0.99, and the voice claims SPY's from
French's market without adjustment.

3. The paths
------------
Path j draws a start session :math:`s_j` uniformly from the sessions where
a window of H = 252 fits, and reads the next H days. On its day k:

.. math::  R^M_{j,k} = \bar r + x_{s_j + k}, \qquad
           R^B_{j,k} = \bar r + \hat b\,x_{s_j + k} + \varepsilon_{j,k},
           \quad \varepsilon_{j,k} \sim N(0, s^2) \text{ independent}

- **Review:** :math:`\bar r` is **today's** bill rate per day, French's
  latest RF, in place of the bill then. A 1981 day's interest isn't today's.
  The excess return is history's; the rate is today's.
- **Review:** the book's own risk is normal and independent of the
  market's day. Its own crashes (an earnings gap) are as likely in a calm
  stretch as in 2008, and its tails beyond the market's come only through
  :math:`\hat b\,x`.
- **Every horizon reads the same path's first h days.**
- ``PATHS`` = 20,000 from a fixed seed. With about 25,000 start sessions,
  most windows are drawn about once.
- **A day's factor** :math:`1 + R` can reach zero (:math:`\hat b \ge 6` on
  19 October 1987, when x was −17%). It's floored at :math:`10^{-6}`:
  that path is wiped out, its return −100% and its fall 100%.

4. The claims
-------------
From the paths' daily log returns :math:`\ln(1 + R)`, read exactly as the
regimes voice reads its own (``voices.path_claims``), for the book and the
market:
- the return over 5, 21, 63 and 252 sessions;
- the chance of ending 10% and 20% down;
- realized volatility over 21 sessions;
- the deepest fall's quantiles, and its chances of 10% and 20%.

**What it changes from the baseline:** history's drift, its crashes and
its fat tails, in place of a driftless normal walk at recent volatility.

**It ignores today's volatility:** a calm today and a stormy today get the
same forecast. That's the opposite of GARCH's assumption, which is why
both are worth scoring. Scaling history by today's volatility (filtered
historical simulation) would be a fourth assumption, and isn't this
voice's.

**Review:** the windows overlap, so a century holds about 99 independent
years. The 252-session tail rests on few crashes: 1929–32, 1937, 1973–74,
1987, 2000–02, 2008, 2020.

**Review:** the century's average excess return is its forecast of the
next year's.
- **The case for:** Welch and Goyal (2008) found that almost no predictor
  they tested beat the historical average out of sample.
- **The case against:** the US century was a good one, and the premium
  ahead may be smaller.

The record will say.

**Proposal: hold.** It forecasts; it has no view on what to change.

**Papers:** Sharpe (1964) for β, Welch and Goyal (2008) for the historical
average as the forecast.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from quant_hub.data.desk import Book, Proposal, VoiceOutput
from quant_hub.models.baseline import TRADING_DAYS, book_log_returns, market_log_returns
from quant_hub.models.voices import LONGEST, MIN_SESSIONS, path_claims

NAME, VERSION = "tail", "1"
PAPERS = ("sharpe1964", "welchgoyal2008")
PATHS = 20_000
SEED = 20261127
MIN_YEARS = 20                   # of the market's history, at the least
FLOOR = 1e-6                     # a day's factor 1 + R at or below zero: wiped out


@dataclass(frozen=True)
class Century:
    """The market's daily excess returns since July 1926 (French's
    Mkt−RF, fractions) and today's bill rate per day (French's latest RF)."""

    excess: np.ndarray
    rate: float
    first: date
    last: date

    def __post_init__(self) -> None:
        if len(self.excess) < MIN_YEARS * TRADING_DAYS:
            raise ValueError(f"{len(self.excess)} sessions of the market's history, "
                             f"{MIN_YEARS} years needed")
        if not np.isfinite(self.excess).all() or not math.isfinite(self.rate):
            raise ValueError("the market's history has a gap or a bad value")

    @staticmethod
    def from_series(excess: pd.Series, bills: pd.Series) -> Century:
        """From French's two daily series, on the days both have."""
        frame = pd.concat({"x": excess, "r": bills}, axis=1, join="inner").dropna()
        if frame.empty:
            raise ValueError("French's market and bills share no days")
        return Century(frame["x"].to_numpy(dtype=float), float(frame["r"].iloc[-1]),
                       frame.index[0].date(), frame.index[-1].date())


@dataclass(frozen=True)
class BetaFit:
    """Section 1: the book on the market, by OLS on daily simple returns."""

    beta: float
    residual_sd: float           # s, a day
    beta_se: float               # s / (σ̂_M √n)
    alpha_annual: float          # dropped (module docstring), reported
    sessions: int


def fit_beta(book_simple: np.ndarray, market_simple: np.ndarray) -> BetaFit:
    """OLS of the book's daily simple returns on the market's (section 1)."""
    y, x = np.asarray(book_simple, float), np.asarray(market_simple, float)
    n = len(y)
    if n < MIN_SESSIONS:
        raise ValueError(f"{n} sessions of the book beside the market, {MIN_SESSIONS} needed")
    xc = x - x.mean()
    sxx = float(xc @ xc)
    if not sxx > 0:
        raise ValueError("the market didn't move")
    beta = float(xc @ (y - y.mean())) / sxx
    alpha = float(y.mean() - beta * x.mean())
    residuals = y - alpha - beta * x
    s = math.sqrt(float(residuals @ residuals) / (n - 2))
    return BetaFit(beta, s, s / math.sqrt(sxx), alpha * TRADING_DAYS, n)


def paths(century: Century, beta: float, residual_sd: float, rng: np.random.Generator,
          n_paths: int = PATHS, days: int = LONGEST) -> tuple[np.ndarray, np.ndarray]:
    """Section 3: the market's and the book's daily log returns, each of
    shape (n_paths, days), from windows of the century drawn uniformly."""
    starts = rng.integers(0, len(century.excess) - days + 1, n_paths)
    window = century.excess[starts[:, None] + np.arange(days)]
    market = century.rate + window
    book = century.rate + beta * window + residual_sd * rng.standard_normal((n_paths, days))
    return np.log(np.maximum(1.0 + market, FLOOR)), np.log(np.maximum(1.0 + book, FLOOR))


def tail(book: Book, log_returns: pd.DataFrame, century: Century) -> VoiceOutput:
    """The century, scaled by the book's β (module docstring)."""
    market = market_log_returns(book, log_returns)
    if market is None:
        raise ValueError("it needs the market (SPY) priced in the snapshot, for β")
    joint = pd.concat([book_log_returns(book, log_returns), market], axis=1).dropna()
    fit = fit_beta(np.expm1(joint.iloc[:, 0].to_numpy()), np.expm1(joint.iloc[:, 1].to_numpy()))
    market_daily, book_daily = paths(century, fit.beta, fit.residual_sd,
                                     np.random.default_rng(SEED))
    claims = path_claims("book", book_daily) + path_claims(book.market, market_daily)
    premium = float(np.mean(century.excess)) * TRADING_DAYS
    why = (f"The century, scaled by your β. The next year drawn from the market's own "
           f"{century.first.year}–{century.last.year}: its drift (about {premium:.1%} a year "
           f"over bills), crashes and fat tails, with the book at β {fit.beta:.2f} and its "
           f"own risk at {fit.residual_sd * math.sqrt(TRADING_DAYS):.1%} a year. Today's "
           f"volatility isn't in it.")
    return VoiceOutput(NAME, VERSION, tuple(claims), Proposal(hold=True), why,
                       {"beta": round(fit.beta, 4), "beta_se": round(fit.beta_se, 4),
                        "residual_sd_daily": round(fit.residual_sd, 8),
                        "alpha_annual_dropped": round(fit.alpha_annual, 4),
                        "sessions": fit.sessions, "rate_daily": century.rate,
                        "history": f"{century.first.isoformat()} to {century.last.isoformat()}",
                        "paths": PATHS, "seed": SEED})
