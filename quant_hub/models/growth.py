r"""Growth and streams: the owner's thesis as numbers (W3).

The derivation below was reviewed before the code (2026-10-21), as W2's
was. The thesis (REGIME.md §1): a core beta stream, plus hedge and return
streams orthogonal to it, sized for CAGR. For today's holdings this module
answers:
- Which positions and sleeves add to the portfolio's growth, and which
  cost it?
- Does each clear its hurdle, or is it too big at the margin?
- How big should a sleeve be, and how sure can we be?
- Which hedge size makes the portfolio's risk smallest?
- What do the options amount to: aggregate greeks and carry?

A sleeve is a group of positions. Until W7 stores the owner's sleeves
(core, hedge, stream), each underlying is one: its shares plus its
options. Every formula below takes any grouping.

Inputs: the replay, position by position
----------------------------------------
The Portfolio model already replays today's holdings over the lookback:
- stocks move by their returns
- options are repriced in full, one day nearer expiry

W3 needs that replay **per position**. Let :math:`p_{i,t}` be position
:math:`i`'s P&L on day :math:`t` as a fraction of equity, and :math:`v_i`
its value share (shares at market, or an option's premium). Its excess
series and the portfolio's are

.. math::  e_{i,t} = p_{i,t} - v_i\,r/252, \qquad R_t = \sum_i e_{i,t},

with cash earning :math:`r` (W2's convention, D44).

**Why series, not weights and a covariance matrix.** The plan proposed
:math:`g(w) = r + w^\top(\mu - r) - \tfrac12 w^\top\Sigma w`, with options
counted by delta. Simulation showed that the quadratic in μ and σ isn't
the weak point:
- on daily data it matches realized log growth to a few thousandths of a
  percent (below)
- the weak point is the delta: it treats an option as stock

Each option's replay series carries its convexity in full. So every
quantity below is built from the series, and the closed form is what they
reduce to for stocks.

Growth
------
With :math:`\bar e` and :math:`s^2` the mean and variance of the daily
:math:`R_t`, the growth rate and CAGR are

.. math::  g = r + 252\,\bigl(\bar e - \tfrac12 s^2\bigr), \qquad
           \text{CAGR} = e^{g} - 1,

and the volatility drag is :math:`126\,s^2`. For stocks this is exactly
:math:`g(w)` at the sample moments.

The quadratic drops third and higher moments of daily returns, which are
small. Simulated:
- a two-year stock sample: 3.4015% quadratic against 3.4008% realized log
  growth
- a put-protected portfolio with 60%-vol crash days: +4.535% against
  +4.537%

The page shows realized log growth, :math:`252\,\overline{\log(1+R_t)}`,
beside :math:`g`, so any gap is visible rather than assumed away.

Attribution: who adds growth (exact)
------------------------------------
Covariance is linear, so :math:`\sum_i \mathrm{Cov}(e_i, R) =
\mathrm{Var}(R)`. That splits the excess growth exactly:

.. math::  g - r = 252 \sum_i \Bigl[\bar e_i - \tfrac12\,\mathrm{Cov}(e_i, R)\Bigr]
           \equiv \sum_i GC_i .

A sleeve's contribution is the sum over its positions. Each term is the
position's excess return less half its share of the portfolio's variance,
the same split the Euler risk contributions make. So :math:`GC_i > 0` iff
:math:`\bar e_i > \tfrac12\mathrm{Cov}(e_i, R)`. A hedge with negative carry
can still add growth if its covariance with the portfolio is negative
enough.

The hurdle: is it too big at the margin?
----------------------------------------
Scaling position :math:`i` by :math:`1 + \delta` scales its series by the
same factor. That's exact for stocks, and for options too, since P&L is
linear in contracts. So

.. math::  \frac{\partial g}{\partial \delta_i} = 252\,\bigl[\bar e_i - \mathrm{Cov}(e_i, R)\bigr],

which is positive iff the position's excess return beats its covariance
with the portfolio: **the hurdle**. For a stock in weight terms, that's
:math:`(\mu_i - r) - \mathrm{Cov}(R_i, R_p)`.

The attribution halves the covariance and the margin doesn't. So a
position can add growth overall (:math:`GC_i > 0`) yet subtract at the
margin. It is then **oversized**: smaller would grow faster. The page
labels each position *adds*, *oversized* or *costs*.

Sizing a sleeve: two answers, one reliable
------------------------------------------
Scale sleeve :math:`S` by :math:`c` with everything else fixed
(:math:`c = 1` is today). With :math:`R_{-S}` the rest of the portfolio,
growth is quadratic in :math:`c`:

.. math::  g(c) = \text{const} + 252\Bigl[c\,\bar e_S
           - \tfrac12\bigl(c^2\,\mathrm{Var}(e_S) + 2c\,\mathrm{Cov}(e_S, R_{-S})\bigr)\Bigr].

Two sizes come out of it:
- **The minimum-variance size** minimizes the portfolio's variance:
  :math:`c_{\mathrm{mv}} = -\mathrm{Cov}(e_S, R_{-S}) / \mathrm{Var}(e_S)`. It's
  minus the regression slope of :math:`R_{-S}` on :math:`e_S`, the hedge
  ratio.
- **The growth-optimal size** maximizes :math:`g`:

  .. math::  c^* = \frac{\bar e_S - \mathrm{Cov}(e_S, R_{-S})}{\mathrm{Var}(e_S)}
             = c_{\mathrm{mv}} + \frac{\bar e_S}{\mathrm{Var}(e_S)} .

  It's the minimum-variance hedge shifted by the sleeve's own Kelly
  fraction. A hedge with negative carry should therefore be smaller than
  its variance-minimizing size, and a stream with positive carry larger.

**Fractional sizing.** Relative to holding none of the sleeve,
:math:`c = f c^*` earns :math:`f(2 - f)` of the growth gain: half size
keeps 75%.

**How well each is known.** Simulated on two years of daily data, in a
world with a core (18% vol), a hedge (25% vol, −5% excess return,
correlation −0.5) and an orthogonal stream:

- :math:`\hat c_{\mathrm{mv}}` is a regression slope, so its error comes
  from W2's Newey–West machinery. True value 2.52, estimates 2.52 ± 0.20,
  and the 95% intervals covered 94.7%.
- :math:`\hat c^*` is dominated by the noise in :math:`\bar e_S`:
  :math:`\mathrm{SE} \approx 1/(\sigma_S\sqrt{T})`, in units of today's
  size, with :math:`\sigma_S` the sleeve's annual vol at today's size and
  :math:`T` in years. That's D39's Kelly error. The intervals covered
  94.9%, but the error was **28 times today's size**: the estimate scatters
  141 times more widely than :math:`\hat c_{\mathrm{mv}}`.

So the page leads with :math:`c_{\mathrm{mv}}` and the variance it would
remove, and shows :math:`c^*` only with its error. The mean's error uses
the Newey–West long-run variance: W2's ``ols`` on a constant.

The thesis in one line
----------------------
With mutually orthogonal streams (diagonal :math:`\Sigma`), each sized at
its own optimum, :math:`g^* = r + \tfrac12\sum_k \mathrm{SR}_k^2`: each
stream adds half its squared Sharpe ratio. That's checked exactly in the
tests. W2's residual links test whether "orthogonal" holds, and this
module says what each stream is worth.

What-if (read-only)
-------------------
:math:`g` is an exact quadratic in any sleeve's scale, so "this sleeve
:math:`x\%` bigger" gives the exact change in growth and vol within the
replay. Nothing is traded; the owner places orders by hand (D12).

Options: aggregate greeks and carry
-----------------------------------
Greeks are linear in contracts, so the book's greeks are signed sums over
legs, in D25's units (vega per vol point, theta per calendar day). With
:math:`c` contracts, multiplier :math:`m` and spot :math:`S`:
- **dollar delta:** :math:`\sum c\,m\,\Delta\,S`
- **gamma P&L of a 1% move:** :math:`\sum c\,m\,\tfrac12\Gamma (0.01 S)^2`
- **vega:** :math:`\sum c\,m\,\mathcal V` dollars per vol point
- **theta:** :math:`\sum c\,m\,\Theta` dollars per day
- **annual carry:** 365 × theta. That's REGIME.md's hedge budget, as a
  share of equity.

Limitations
-----------
- The replay is today's holdings over the lookback, not the account's
  history.
- Options are repriced at the underlying's realized vol, with no smile
  (as in the Portfolio model).
- Means are noisy, so every growth number carries its error. Variances
  are known far better.
- Errors are asymptotic, with Newey–West for serial dependence.
- No transaction costs or taxes in the what-if.

API sketch
----------
- ``growth_report(excess, rate, sleeves=None) -> GrowthReport``. From the
  per-position excess series (a DataFrame, fractions of equity):
  - :math:`g`, realized log growth, CAGR, drag
  - per sleeve: contribution, margin, verdict, :math:`\bar e` ± SE,
    :math:`c_{\mathrm{mv}}` ± SE and the variance it removes,
    :math:`c^*` ± SE
- ``what_if(report, sleeve, scale) -> (growth change, vol change)``.
- ``aggregate_greeks(options) -> BookGreeks``.
- ``models.portfolio.risk_report`` gains the per-position replay series.
  It already computes each one before summing them.

Validation plan (tests)
-----------------------
1. **Attribution:** :math:`\sum_i GC_i = g - r` to 1e-12, with options in
   the book.
2. **Margin:** matches finite differences of rescaled series, for a stock
   and an option.
3. **Closed form:** for stocks only, :math:`g`, :math:`GC_i` and the margin
   equal the :math:`w, \mu, \Sigma` formulas at the sample moments.
4. **Sizing:**
   - :math:`c^*` agrees with a grid search
   - :math:`c^* = c_{\mathrm{mv}} + \bar e_S/\mathrm{Var}(e_S)`
   - fractional :math:`f(2 - f)`
5. **Coverage** (seeded, 2,000 runs of two years): both
   :math:`\hat c^*` and :math:`\hat c_{\mathrm{mv}}` intervals cover 95%
   within binomial tolerance, and :math:`\hat c^*`'s error is at least 10
   times :math:`\hat c_{\mathrm{mv}}`'s in the hedge world above.
6. **Orthogonal streams:** :math:`g^* = r + \tfrac12\sum \mathrm{SR}^2`
   under a diagonal :math:`\Sigma`.
7. **Approximation:** quadratic and realized log growth agree within 0.01%
   a year on two-year stock samples.
8. **Options:** a position's series scales exactly with contracts; book
   greeks equal the per-leg sums and match finite-difference repricing.
9. **Rejections:** empty or non-finite series, sleeves naming unknown
   positions, a sleeve with no variance.

The page (W3's second step)
---------------------------
On the Portfolio page:
- **Growth:** :math:`g` and realized log growth, CAGR, drag
- **Sleeves table:** weight, contribution (basis points a year), verdict,
  :math:`c_{\mathrm{mv}}` ± SE, :math:`c^*` ± SE
- **What-if:** a sleeve and a scale, giving the change in growth and vol
- **Options:** book greeks and annual carry, shown when the book holds any

Verdicts, completed in the code
-------------------------------
Contribution and margin give four sign pairs, not the three labels above:
- **adds:** :math:`GC > 0` and margin :math:`> 0`
- **oversized:** :math:`GC > 0`, margin :math:`\le 0`
- **undersized:** :math:`GC \le 0`, margin :math:`> 0`. It costs growth today,
  yet more would help: typically a cheap hedge held too small.
- **costs:** :math:`GC \le 0` and margin :math:`\le 0`

A verdict whose margin is within 1.96 errors of zero is reported as not
significant. A sleeve with no variance has no sizes (NaN), rather than
failing the whole report.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_hub.models.black_scholes import greeks_array
from quant_hub.models.portfolio import MIN_DAYS, TRADING_DAYS, OptionExposure
from quant_hub.models.regression import NEWEY_WEST, Z95, ols

ADDS, OVERSIZED, UNDERSIZED, COSTS = "adds", "oversized", "undersized", "costs"
DAYS_PER_YEAR = 365.0                     # theta is per calendar day (D25)
MOVE = 0.01                               # the gamma P&L is quoted for a 1% move


@dataclass(frozen=True)
class SleeveGrowth:
    """One sleeve's part in the portfolio's growth (module docstring).
    Rates are annual; sizes are multiples of today's size."""

    name: str
    positions: tuple[str, ...]
    value_share: float                    # sum of its value shares v_i
    vol: float                            # sigma_S, annual, at today's size
    contribution: float                   # GC_S, growth units
    margin: float                         # dg/d(delta_S)
    excess_mean: float                    # 252 e-bar_S
    excess_mean_error: float              # Newey–West, annual; also the margin's error
    c_mv: float                           # minimum-variance size
    c_mv_error: float
    vol_at_c_mv: float                    # the portfolio's annual vol at c_mv
    c_star: float                         # growth-optimal size
    c_star_error: float

    @property
    def verdict(self) -> str:
        if self.contribution > 0:
            return ADDS if self.margin > 0 else OVERSIZED
        return UNDERSIZED if self.margin > 0 else COSTS

    @property
    def significant(self) -> bool:
        """Whether the margin is more than 1.96 errors from zero."""
        return abs(self.margin) > Z95 * self.excess_mean_error


@dataclass(frozen=True)
class GrowthReport:
    """The portfolio's growth and each sleeve's part (module docstring)."""

    rate: float
    growth: float                         # g, annual log growth rate
    realized: float                       # 252 mean log(1 + R_t + r/252)
    drag: float                           # 126 s^2
    vol: float                            # annual
    observations: int
    sleeves: tuple[SleeveGrowth, ...]
    excess: pd.DataFrame                  # each sleeve's daily excess series
    total: pd.Series                      # the portfolio's daily excess series

    @property
    def cagr(self) -> float:
        return math.expm1(self.growth)

    def sleeve(self, name: str) -> SleeveGrowth:
        for sleeve in self.sleeves:
            if sleeve.name == name:
                return sleeve
        raise KeyError(f"no sleeve {name!r}")


@dataclass(frozen=True)
class BookGreeks:
    """The options book's greeks in dollars (module docstring, D25 units)."""

    delta: float                          # stock-equivalent dollars
    gamma_move: float                     # P&L of a 1% move from gamma
    vega: float                           # per vol point
    theta: float                          # per calendar day
    contracts: int                        # option positions in the book

    @property
    def carry(self) -> float:
        """Annual theta, dollars: the hedge budget's measure."""
        return DAYS_PER_YEAR * self.theta


def growth_report(excess: pd.DataFrame, rate: float,
                  sleeves: Mapping[str, Sequence[str]] | None = None,
                  values: Mapping[str, float] | None = None) -> GrowthReport:
    """Growth, attribution, hurdles and sizes from per-position daily excess
    series (fractions of equity, in excess of cash at ``rate``/252).
    ``sleeves`` groups positions by name; by default each position is one.
    ``values`` gives each position's value share, for the sleeves' weights."""
    if not isinstance(excess, pd.DataFrame) or excess.shape[1] == 0:
        raise ValueError("excess must be a DataFrame with a column per position")
    if not np.isfinite(excess.to_numpy(dtype=float)).all():
        raise ValueError("excess series must be finite")
    if len(excess) < MIN_DAYS:
        raise ValueError(f"{len(excess)} days of returns; growth needs {MIN_DAYS}")
    if not math.isfinite(rate):
        raise ValueError("rate must be finite")
    groups = ({str(c): (str(c),) for c in excess.columns} if sleeves is None
              else {name: tuple(members) for name, members in sleeves.items()})
    listed = [p for members in groups.values() for p in members]
    unknown = sorted(set(listed) - set(map(str, excess.columns)))
    if unknown:
        raise ValueError(f"sleeves name unknown positions: {unknown}")
    if len(listed) != len(set(listed)):
        raise ValueError("a position belongs to more than one sleeve")
    if any(not members for members in groups.values()):
        raise ValueError("every sleeve needs at least one position")

    excess = excess.rename(columns=str)
    by_sleeve = pd.DataFrame({name: excess[list(members)].sum(axis=1)
                              for name, members in groups.items()})
    # Positions in no sleeve still count in the portfolio.
    total = excess.sum(axis=1).to_numpy(dtype=float)
    mean, variance = float(total.mean()), float(total.var(ddof=1))
    growth = rate + TRADING_DAYS * (mean - 0.5 * variance)
    realized = TRADING_DAYS * float(np.mean(np.log1p(total + rate / TRADING_DAYS)))
    ones = np.ones((len(total), 1))
    reports = []
    for name, members in groups.items():
        e = by_sleeve[name].to_numpy(dtype=float)
        rest = total - e
        cov_total = float(np.cov(e, total, ddof=1)[0, 1])
        cov_rest = float(np.cov(e, rest, ddof=1)[0, 1])
        var_e = float(e.var(ddof=1))
        mean_fit = ols(e, ones, ("mean",))
        mean_error = mean_fit.error("mean", NEWEY_WEST)
        if var_e > 0:
            c_mv = -cov_rest / var_e
            hedge = ols(rest, np.column_stack([ones, e]), ("alpha", "sleeve"))
            c_mv_error = hedge.error("sleeve", NEWEY_WEST)
            var_at = float(rest.var(ddof=1)) + 2 * c_mv * cov_rest + c_mv**2 * var_e
            c_star = (float(e.mean()) - cov_rest) / var_e
            c_star_error = mean_error / var_e
        else:
            c_mv = c_mv_error = var_at = c_star = c_star_error = float("nan")
        reports.append(SleeveGrowth(
            name, members,
            value_share=float(sum((values or {}).get(p, 0.0) for p in members)),
            vol=math.sqrt(var_e * TRADING_DAYS),
            contribution=TRADING_DAYS * (float(e.mean()) - 0.5 * cov_total),
            margin=TRADING_DAYS * (float(e.mean()) - cov_total),
            excess_mean=TRADING_DAYS * float(e.mean()),
            excess_mean_error=TRADING_DAYS * mean_error,
            c_mv=c_mv, c_mv_error=c_mv_error,
            vol_at_c_mv=math.sqrt(max(var_at, 0.0) * TRADING_DAYS) if var_e > 0 else float("nan"),
            c_star=c_star, c_star_error=c_star_error))
    return GrowthReport(rate, growth, realized, 0.5 * TRADING_DAYS * variance,
                        math.sqrt(variance * TRADING_DAYS), len(total), tuple(reports), by_sleeve,
                        pd.Series(total, index=excess.index))


def what_if(report: GrowthReport, sleeve: str, scale: float) -> tuple[float, float]:
    """The change in annual growth and vol if ``sleeve`` were ``scale`` times
    today's size, everything else fixed (exact within the replay)."""
    if not math.isfinite(scale):
        raise ValueError("scale must be finite")
    if sleeve not in report.excess.columns:
        raise KeyError(f"no sleeve {sleeve!r}")
    e = report.excess[sleeve].to_numpy(dtype=float)
    rest = report.total.to_numpy(dtype=float) - e

    def moments(c: float) -> tuple[float, float]:
        series = rest + c * e
        return float(series.mean()), float(series.var(ddof=1))

    (m1, v1), (m2, v2) = moments(1.0), moments(scale)
    growth = TRADING_DAYS * ((m2 - 0.5 * v2) - (m1 - 0.5 * v1))
    vol = math.sqrt(v2 * TRADING_DAYS) - math.sqrt(v1 * TRADING_DAYS)
    return growth, vol


def book_greeks(options: Sequence[OptionExposure]) -> BookGreeks:
    """Signed sums over legs in D25 units (module docstring)."""
    delta = gamma = vega = theta = 0.0
    for o in options:
        g = greeks_array(o.spot, o.strike, max(o.days, 1e-6), o.rate, o.vol, o.option_type)
        size = o.contracts * o.multiplier
        delta += size * float(g.delta) * o.spot
        gamma += size * 0.5 * float(g.gamma) * (MOVE * o.spot) ** 2
        vega += size * float(g.vega)
        theta += size * float(g.theta)
    return BookGreeks(delta, gamma, vega, theta, len(options))
