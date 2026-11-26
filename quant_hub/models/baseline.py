r"""The baseline: what the desk says with no edge at all (D1, D96; D2b).

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
* **The chance of ending down** k or worse:
  :math:`P(R_h \le k) = \Phi\bigl(\ln(1+k) / (\hat\sigma \sqrt h)\bigr)`.
* **Realized volatility over h sessions**, :math:`\sqrt{252 \cdot \tfrac1h
  \sum r_t^2}`. With iid :math:`N(0, \hat\sigma^2)` days,
  :math:`\sum r_t^2 / \hat\sigma^2 \sim \chi^2_h`, so its quantiles are
  :math:`\hat\sigma \sqrt{252\, \chi^2_{h,\tau} / h}`. The χ² quantile is
  exact: the χ² CDF is the regularized lower incomplete gamma function,
  :math:`F_k(x) = P(k/2,\, x/2)`, computed by its series below
  :math:`a + 1` and its continued fraction above (the standard pair), and
  inverted by bisection. The tests check it against closed forms, tables
  and simulation.
* **The deepest fall within h sessions** (D2b), ``book.fall``. On daily
  closes, with :math:`L_t = \sum_{s \le t} r_s` and :math:`L_0 = 0`,

  .. math::  D_h = \max_{0 \le u \le v \le h} \bigl(1 - e^{L_v - L_u}\bigr)
             = 1 - e^{-G_h}, \qquad
             G_h = \max_{t \le h} \bigl(\max_{u \le t} L_u - L_t\bigr)

  :math:`1 - e^{-G}` is increasing, so the deepest fall is the largest gap
  below the running peak, read in logs and turned into a fall once
  (``deepest_falls``). Its law on **daily** closes has no closed form: the
  continuous-time drawdown law (Magdon-Ismail et al. 2004) watches the path between
  closes too, so it overstates a fall the target can't see. So it's
  **simulated**: ``NULL_PATHS`` = 100,000 driftless paths from a fixed
  seed. A probability's standard error is then at most
  :math:`\sqrt{0.25 / 10^5} = 0.0016`, and the same inputs give the same
  claims. This is the only number in the null that isn't exact.

**The targets.** The book, at the horizons in ``HORIZONS`` (5, 21, 63 and
252 sessions), and **the market** (``data/desk.MARKET``, SPY) the same way
at its own :math:`\hat\sigma`, when the snapshot priced it. Each holding
gets its 21-session return.

**What it ignores, on purpose:** fat tails, volatility clustering, the
error in :math:`\hat\sigma`, and any drift. A voice that models those has
to show it scores better. The baseline proposes holding the book as it is.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from statistics import NormalDist

import numpy as np
import pandas as pd

from quant_hub.data.desk import LEVELS, Book, Claim, Proposal, VoiceOutput

NAME, VERSION = "baseline", "2"
# The canon papers it rests on (data/canon): log prices diffuse (Osborne),
# and properly anticipated prices carry no predictable drift (Samuelson).
PAPERS = ("osborne1959", "samuelson1965proof")
WINDOW = 63                     # sessions of history for σ̂
HORIZONS = (5, 21, 63, 252)
FALLS = ((21, -0.10), (63, -0.20))          # ending down k or worse, at h
FALL_HORIZONS = (21, 63, 252)               # the deepest fall's quantiles
FALL_DEPTHS = ((21, 0.10), (63, 0.10), (63, 0.20), (252, 0.10), (252, 0.20))
VOL_HORIZON = 21
TRADING_DAYS = 252
NULL_PATHS = 100_000
NULL_SEED = 20261126
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


def market_log_returns(book: Book, log_returns: pd.DataFrame) -> pd.Series | None:
    """The market's daily log returns, when the snapshot priced it."""
    if not book.market or book.market not in log_returns.columns:
        return None
    return log_returns[book.market].rename(book.market)


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


def deepest_falls(steps: Iterable[np.ndarray], horizons: Iterable[int]) -> dict[int, np.ndarray]:
    """:math:`D_h` for each h in ``horizons`` (module docstring), from daily
    log returns that arrive one session at a time: each step is an array
    with one value per path. One pass, three arrays of state, so 100,000
    paths of a year never sit in memory at once."""
    wanted = sorted(set(horizons))
    out: dict[int, np.ndarray] = {}
    level = peak = gap = None
    t = 0
    for t, step in enumerate(steps, 1):
        step = np.asarray(step, dtype=float)
        if level is None:
            level, peak, gap = (np.zeros_like(step) for _ in range(3))
        level = level + step
        np.maximum(peak, level, out=peak)
        np.maximum(gap, peak - level, out=gap)
        if t in wanted:
            out[t] = -np.expm1(-gap)
            if len(out) == len(wanted):
                break
    if len(out) < len(wanted):
        raise ValueError(f"paths of {t} sessions can't show a fall over {wanted[-1]}")
    return out


def fall_claims(target: str, falls: Mapping[int, np.ndarray]) -> list[Claim]:
    """``target``'s deepest-fall claims from simulated falls: quantiles at
    ``FALL_HORIZONS`` and the chances of ``FALL_DEPTHS``."""
    claims = [Claim(f"{target}.fall", h, tuple(float(np.quantile(falls[h], tau))
                                                for tau in LEVELS)) for h in FALL_HORIZONS]
    claims += [Claim(f"{target}.fall", h, event=f">= {d:g}",
                     probability=float(np.mean(falls[h] >= d))) for h, d in FALL_DEPTHS]
    return claims


def null_falls(sigma: float, rng: np.random.Generator,
               paths: int = NULL_PATHS) -> dict[int, np.ndarray]:
    """The driftless walk's deepest falls at ``FALL_HORIZONS``, simulated
    (module docstring)."""
    longest = max(FALL_HORIZONS)
    return deepest_falls((sigma * rng.standard_normal(paths) for _ in range(longest)),
                         FALL_HORIZONS)


def walk_claims(target: str, sigma: float, rng: np.random.Generator) -> list[Claim]:
    """The no-edge claims about one target at volatility ``sigma`` a day."""
    claims = [Claim(f"{target}.return", h, return_quantiles(sigma, h)) for h in HORIZONS]
    claims += [Claim(f"{target}.return", h, event=f"<= {k:g}",
                     probability=fall_probability(sigma, h, k)) for h, k in FALLS]
    claims.append(Claim(f"{target}.vol", VOL_HORIZON, vol_quantiles(sigma, VOL_HORIZON)))
    return claims + fall_claims(target, null_falls(sigma, rng))


def sigma_hat(series: pd.Series, window: int = WINDOW) -> float:
    tail = series.dropna().iloc[-window:]
    if len(tail) < window:
        raise ValueError(f"{series.name}: {len(tail)} sessions of returns, {window} needed")
    sigma = float(tail.std(ddof=1))
    if not sigma > 0:
        raise ValueError(f"{series.name}: no movement in {window} sessions")
    return sigma


def baseline(book: Book, log_returns: pd.DataFrame, window: int = WINDOW) -> VoiceOutput:
    """The no-edge voice's claims about ``book`` and the market (module
    docstring)."""
    rng = np.random.default_rng(NULL_SEED)
    series = book_log_returns(book, log_returns)
    sigma = sigma_hat(series, window)
    claims = walk_claims("book", sigma, rng)
    parameters = {"window": window, "sigma_daily": round(sigma, 10), "paths": NULL_PATHS,
                  "seed": NULL_SEED}
    market = market_log_returns(book, log_returns)
    market_sigma = None
    if market is not None:
        try:
            market_sigma = sigma_hat(market, window)
        except ValueError:
            market_sigma = None
    if market_sigma is not None:
        claims += walk_claims(book.market, market_sigma, rng)
        parameters["market_sigma_daily"] = round(market_sigma, 10)
    for holding in book.holdings:
        if market_sigma is not None and holding.symbol == book.market:
            continue                    # the market's claims already include its return
        s = sigma_hat(log_returns[holding.symbol].rename(holding.symbol), window)
        claims.append(Claim(f"{holding.symbol}.return", 21, return_quantiles(s, 21)))
    why = (f"No edge: a random walk with no drift at the book's last {window} sessions' "
           f"volatility, {sigma * math.sqrt(TRADING_DAYS):.1%} a year. Every voice is "
           "scored against this.")
    return VoiceOutput(NAME, VERSION, tuple(claims), Proposal(hold=True), why, parameters)
