r"""The baseline: what the desk says with no edge at all (D1, D96).

Every voice on the desk is scored against this one. It knows only how much
the book has been moving lately, and assumes nothing about direction. A
model that can't beat it on a target has nothing to say about that target.

The model
---------
Today's book, weights :math:`w_i` with cash earning nothing, replayed over
the past: its daily log return is

.. math::  r_t = \ln\Bigl(1 + \sum_i w_i\,(e^{\,r_{i,t}} - 1)\Bigr)

These are today's weights applied to the past, which is right for a
forecast of today's book. Its volatility is the sample standard deviation
of the last W = 63 sessions, :math:`\hat\sigma`.

**The no-edge law** is a random walk with no drift:
:math:`\ln(V_{t+h}/V_t) \sim N(0, \hat\sigma^2 h)`. A drift would be a view
on direction, and that is what other voices have to earn. So:

* **Return over h sessions**, at level τ:
  :math:`q_\tau = e^{\,z_\tau \hat\sigma \sqrt h} - 1`.
* **The chance of a fall** to k or worse:
  :math:`P(R_h \le k) = \Phi\bigl(\ln(1+k) / (\hat\sigma \sqrt h)\bigr)`.
* **Realized volatility over h sessions**, :math:`\sqrt{252 \cdot \tfrac1h
  \sum r_t^2}`. With iid :math:`N(0, \hat\sigma^2)` days,
  :math:`\sum r_t^2 / \hat\sigma^2 \sim \chi^2_h`, so its quantiles are
  :math:`\hat\sigma \sqrt{252\, \chi^2_{h,\tau} / h}`. The χ² quantile is
  exact: the χ² CDF is the regularized lower incomplete gamma function,
  :math:`F_k(x) = P(k/2,\, x/2)`, computed by its series below
  :math:`a + 1` and its continued fraction above (the standard pair), and
  inverted by bisection. The null holds no approximation; the tests check
  it against closed forms, tables and simulation.

The same three apply to each holding alone, at its own :math:`\hat\sigma`,
for its 21-session return.

**What it ignores, on purpose:** fat tails, volatility clustering, the
error in :math:`\hat\sigma`, and any drift. A voice that models those has
to show it scores better. The baseline proposes holding the book as it is.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from quant_hub.data.desk import LEVELS, Book, Claim, Proposal, VoiceOutput

NAME, VERSION = "baseline", "1"
# The canon papers it rests on (data/canon): log prices diffuse (Osborne),
# and properly anticipated prices carry no predictable drift (Samuelson).
PAPERS = ("osborne1959", "samuelson1965proof")
WINDOW = 63                     # sessions of history for σ̂
HORIZONS = (5, 21, 63)
FALLS = ((21, -0.10), (63, -0.20))
TRADING_DAYS = 252
_Z = [NormalDist().inv_cdf(tau) for tau in LEVELS]


def book_log_returns(book: Book, log_returns: pd.DataFrame) -> pd.Series:
    """Today's book replayed over the past (module docstring). ``log_returns``
    holds each priced holding's daily log returns, one column per symbol."""
    weights = book.weights()
    missing = [s for s in weights if s not in log_returns.columns]
    if missing:
        raise ValueError(f"no returns for {', '.join(missing)}")
    w = np.array([weights[s] for s in log_returns.columns if s in weights])
    simple = np.expm1(log_returns[[s for s in log_returns.columns if s in weights]].to_numpy())
    return pd.Series(np.log1p(simple @ w), index=log_returns.index, name="book")


def gamma_p(a: float, x: float) -> float:
    """The regularized lower incomplete gamma function P(a, x): its series
    for x < a + 1, else 1 minus its continued fraction (modified Lentz)."""
    if x <= 0:
        return 0.0
    log_front = a * math.log(x) - x - math.lgamma(a)
    if x < a + 1:
        term = total = 1.0 / a
        n = a
        while abs(term) > abs(total) * 1e-16:
            n += 1
            term *= x / n
            total += term
        return min(1.0, total * math.exp(log_front))
    tiny = 1e-300
    b = x + 1 - a
    c, d = 1 / tiny, 1 / b
    h = d
    for i in range(1, 10_000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-16:
            break
    return max(0.0, 1.0 - math.exp(log_front) * h)


def chi2_quantile(k: int, tau: float) -> float:
    """The τ quantile of χ² with k degrees of freedom: P(k/2, x/2) = τ,
    solved by bisection to near machine precision."""
    lo, hi = 0.0, k + 40 * math.sqrt(2 * k) + 100
    for _ in range(200):
        mid = (lo + hi) / 2
        if gamma_p(k / 2, mid / 2) < tau:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-12 * max(1.0, hi):
            break
    return (lo + hi) / 2


def return_quantiles(sigma: float, h: int) -> tuple[float, ...]:
    return tuple(math.expm1(z * sigma * math.sqrt(h)) for z in _Z)


def fall_probability(sigma: float, h: int, k: float) -> float:
    return NormalDist().cdf(math.log1p(k) / (sigma * math.sqrt(h)))


def vol_quantiles(sigma: float, h: int) -> tuple[float, ...]:
    return tuple(sigma * math.sqrt(TRADING_DAYS * chi2_quantile(h, tau) / h) for tau in LEVELS)


def sigma_hat(series: pd.Series, window: int = WINDOW) -> float:
    tail = series.dropna().iloc[-window:]
    if len(tail) < window:
        raise ValueError(f"{series.name}: {len(tail)} sessions of returns, {window} needed")
    sigma = float(tail.std(ddof=1))
    if not sigma > 0:
        raise ValueError(f"{series.name}: no movement in {window} sessions")
    return sigma


def baseline(book: Book, log_returns: pd.DataFrame, window: int = WINDOW) -> VoiceOutput:
    """The no-edge voice's claims about ``book`` (module docstring)."""
    series = book_log_returns(book, log_returns)
    sigma = sigma_hat(series, window)
    claims = [Claim("book.return", h, return_quantiles(sigma, h)) for h in HORIZONS]
    claims += [Claim("book.return", h, event=f"<= {k:g}",
                     probability=fall_probability(sigma, h, k)) for h, k in FALLS]
    claims.append(Claim("book.vol", 21, vol_quantiles(sigma, 21)))
    for holding in book.holdings:
        s = sigma_hat(log_returns[holding.symbol].rename(holding.symbol), window)
        claims.append(Claim(f"{holding.symbol}.return", 21, return_quantiles(s, 21)))
    why = (f"No edge: a random walk with no drift at the book's last {window} sessions' "
           f"volatility, {sigma * math.sqrt(TRADING_DAYS):.1%} a year. Every voice is "
           "scored against this.")
    return VoiceOutput(NAME, VERSION, tuple(claims), Proposal(hold=True), why,
                       {"window": window, "sigma_daily": round(sigma, 10)})
