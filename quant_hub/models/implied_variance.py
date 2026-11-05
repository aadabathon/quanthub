r"""Model-free implied variance: the price of variance read off an option chain.

VARIANCE I1 (VARIANCE_PLAN.md). The derivation below was in the plan for
review before the code. Two estimators come from one result, and each
checks the other: one integrates a fitted smile, the other sums the quotes.

1. Spanning, and the variance strike
------------------------------------
For any twice-differentiable payoff :math:`f` and any level :math:`F`
(Carr & Madan 1998), by Taylor's theorem with the remainder in integral form:

.. math::  f(S_T) = f(F) + f'(F)(S_T - F)
           + \int_0^F f''(K)(K - S_T)^+\,dK + \int_F^\infty f''(K)(S_T - K)^+\,dK.

Take :math:`f = \ln` and :math:`F` the forward, so :math:`E^Q[S_T] = F` and
the linear term has zero expectation. With :math:`P`, :math:`C` the put and
call prices and :math:`D` the discount factor (both per expiry, from
``OptionChain.forward``/``discount``, which carry the parity fit of
``models/carry.py``):

.. math::  -E^Q[\ln(S_T/F)] = \frac{1}{D}\left[\int_0^F \frac{P(K)}{K^2}\,dK
           + \int_F^\infty \frac{C(K)}{K^2}\,dK\right].

If the price moves continuously, :math:`dS/S = (r - q)\,dt + \sigma_t\,dW`,
Itô gives :math:`\ln(S_T/F) = -\tfrac12\int_0^T\sigma_t^2\,dt + \int_0^T\sigma_t\,dW`,
so the risk-neutral expected integrated variance (Demeterfi, Derman, Kamal
& Zou 1999; Britten-Jones & Neuberger 2000) is

.. math::  K_{var}\,T \equiv E^Q\left[\int_0^T \sigma_t^2\,dt\right]
           = \frac{2}{D}\left[\int_0^F \frac{P}{K^2}\,dK + \int_F^\infty \frac{C}{K^2}\,dK\right].

With :math:`D = e^{-rT}` this is the VIX's :math:`2e^{rT}/T` (CBOE's white
paper). No model of :math:`\sigma_t` is assumed: only continuity, and that
the quotes are European. Everything here is in *total* variance
:math:`K_{var}T`, with :math:`T` = days/365 as the chain's ``dte`` is. The
annualized variance is the total over :math:`T`, and the vol its root.

2. In the smile's own units
---------------------------
Write :math:`k = \ln(K/F)`, :math:`w(k)` the smile's total implied
variance, and :math:`b(k, w)` the Black price of the out-of-the-money option
in units of :math:`DF` (the call for :math:`k \ge 0`, the put below):

.. math::  c(k, w) = \Phi(d_1) - e^k\Phi(d_2), \qquad
           p(k, w) = e^k\Phi(-d_2) - \Phi(-d_1), \qquad
           d_{1,2} = -\frac{k}{\sqrt w} \pm \frac{\sqrt w}{2}.

Then :math:`P = DF\,p`, :math:`K = Fe^k`, :math:`dK = Fe^k\,dk`, and
:math:`F` and :math:`D` cancel:

.. math::  K_{var}\,T = 2\int_{-\infty}^{\infty} e^{-k}\,b(k, w(k))\,dk.

Only the total-variance smile enters, so ``smile_variance`` integrates each
fitted slice (``SurfaceFit``'s raw SVI, from either fit method) directly.

**The integral.** Composite Simpson on ``INTERVALS`` intervals per piece,
with the pieces broken at :math:`k = 0`, where :math:`b` has a kink
(:math:`c - p = 1 - e^k`, so their slopes differ by :math:`e^0 = 1`), and
at the quoted range's ends. Far out, the integrand is negligible. The
range runs to where :math:`z = -d_2` (below) reaches :math:`\mp`
``TAIL_Z`` = 8, found by doubling :math:`|k|` from one ATM sd. Past that
the omitted share is of order :math:`\varphi(8) \approx 5\times10^{-15}`.
:math:`z \to \pm\infty` in both wings exactly when their slopes stay
below Lee's bound of 2, which raw SVI enforces (``svi.RawSVI``).

**An equivalent form** (Fukasawa 2012; Gatheral 2006), with
:math:`z = -d_2 = k/\sqrt w + \sqrt w/2`:

.. math::  K_{var}\,T = \int_{-\infty}^{\infty} \varphi(z)\,w(k(z))\,dz.

The variance strike is the smile's total variance averaged under a standard
normal in :math:`z`. A flat smile gives :math:`\sigma^2T` exactly, and a
skew gives more than the ATM variance. :math:`z` must rise with :math:`k`,
which absence of butterfly arbitrage guarantees. ``normal_average``
computes this form (:math:`k(z)` by bisection) as the test of the first.
The two agree to about :math:`10^{-11}` on the plan's slices.

3. From the quotes directly (the VIX way)
-----------------------------------------
:math:`K_0` is the highest strike at or below :math:`F` with a two-sided
put. Puts are taken below :math:`K_0`, calls above it, and their average
at :math:`K_0`:

.. math::  K_{var}\,T \approx \frac{2}{D}\sum_i \frac{\Delta K_i}{K_i^2}\,Q(K_i)
           - 2\left[\frac{F}{K_0} - 1 - \ln\frac{F}{K_0}\right],

with :math:`\Delta K_i = (K_{i+1} - K_{i-1})/2`, one-sided at the ends.

**The last term.** Between :math:`K_0` and :math:`F` the sum uses calls
where the formula wants puts. Parity (:math:`C - P = D(F - K)`) makes the
excess :math:`\frac{2}{D}\int_{K_0}^F D(F - K)/K^2\,dK`, which is the
bracket above. CBOE uses its second-order form :math:`(F/K_0 - 1)^2`; the
exact one costs nothing. At :math:`K_0`, a call that isn't quoted
two-sided is the put plus :math:`D(F - K_0)` by the same parity.

**Which quotes.** Out-of-the-money quotes with a bid, as CBOE's rule has
it: walking out from :math:`K_0`, a strike with no bid is skipped, and two
in a row end that side. A strike with no quote of the needed type isn't
listed and is passed over.

**A band from the quotes alone.** Every weight is positive. So the sum at
bids and the sum at asks bound the quoted strikes' contribution, with no
model. The last term doesn't depend on prices, so it shifts all three
sums alike.

**The two estimators check each other.** The sum differs from the integral
over the same range by discretization and by the half-strips past the end
strikes (Jiang & Tian 2005). So the like-for-like check sums the *fitted*
smile's prices on the same strikes with the same weights. If every fitted
price lies inside its quote, that sum lies inside the band. A fit outside
the band is flagged (``QuoteSum.inside``).

As strikes densify, the sum tends to the integral over the quoted strikes'
range, not over the whole smile. With 5-point strikes over −6 to +3 ATM sd
on the slice below, both give 16.4%.

4. Where the answer comes from: quoted range and wings
------------------------------------------------------
Quotes stop somewhere, and the missing wings only ever lower the sum.
So ``smile_variance`` splits :math:`K_{var}T` in two:

* the quoted range, between the slice's quoted :math:`k_{min}` and
  :math:`k_{max}`;
* the wings, SVI's linear wings extrapolated past them, within Lee's
  bound (Lee 2004).

The extrapolated share is shown beside the number. On the plan's
equity-like 30-day slice (ATM 14%, :math:`\rho = -0.7`, :math:`\eta = 1`),
√K_var is 16.6%, and a request band of −2.5 to +1.5 ATM sd holds 86% of it.
At a steeper skew (:math:`\eta = 1.5`), 19.3%, and that band holds 73%.
Stress steepens skews, so the miss is worst when the number matters most.

5. What the number is not: jumps
--------------------------------
With jumps :math:`J` the log contract and realized variance part ways:

.. math::  -2E^Q[\ln(S_T/F)] = E\left[\int\sigma^2\,dt\right] + 2E\left[\sum(e^J - 1 - J)\right],
           \qquad
           E^Q[\text{realized variance}] = E\left[\int\sigma^2\,dt\right] + E\left[\sum J^2\right].

Per jump :math:`2(e^J - 1 - J)/J^2` is 0.984 at −5% and 0.937 at −20%, so
in a crash-prone market this sits slightly below a true variance swap rate.
On the plan's Merton chain (σ 12%, half a jump a year, mean −8%, sd 10%,
91 days): 14.88% against 15.03%. Hence the name "model-free implied
variance" (Britten-Jones & Neuberger 2000), never "variance swap rate". It
slightly understates a variance premium.

6. Across expiries
------------------
**Constant maturity:** total variance linear in :math:`T` between the two
fitted expiries that bracket the tenor,

.. math::  w(\tau) = \lambda w_1 + (1 - \lambda) w_2, \qquad
           \lambda = \frac{T_2 - \tau}{T_2 - T_1}.

This is the VIX's rule and ``surface_mesh``'s. It is exact for variance,
which adds over time: if the forward variance is constant between the two
expiries, the interpolation recovers :math:`w(\tau)` exactly. It never
extrapolates in :math:`T`. The quoted and wing parts, and the quote sums
with their band, interpolate the same way (all weights are positive).

**Forward variance between expiries:**
:math:`(w_2 - w_1)/(T_2 - T_1)`. It must be non-negative: the calendar
check, in variance terms.

Validation plan (tests)
-----------------------
1. A flat smile gives :math:`\sigma^2` exactly, at every expiry.
2. The k-form equals the z-form on fitted SVI and joint-SSVI slices.
3. The plan's worked example: 16.6%, the band shares, the VIX sum on
   5-point strikes, and the steep skew.
4. On the synthetic SSVI world, the fit's like-for-like sum lies inside the
   quotes' band, and the whole-smile estimate is close to the true
   surface's.
5. On a Merton chain, the spanning integral matches
   :math:`-2E[\ln S_T/F]`'s closed form to :math:`10^{-6}`, and sits below
   :math:`E[QV]` by section 5's jump term.
6. The VIX sum converges as strikes densify, and truncation only lowers it.
7. Interpolation is exact on additive variance; forward variances and the
   calendar flag; no extrapolation in :math:`T`.
8. Inputs are validated.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from quant_hub.data.options import OptionChain, OptionQuote
from quant_hub.models.svi import RawSVI
from quant_hub.models.vol_surface import SurfaceFit

DAYS_PER_YEAR = 365.0
"""Calendar days per year, as the chain's ``dte`` and the fits count them."""

TAIL_Z = 8.0
"""How far the integral runs, in :math:`z = -d_2`: :math:`\\varphi(8) \\approx 5\\times10^{-15}`."""

INTERVALS = 4000
"""Simpson intervals per piece of the integral (even)."""

MAX_LOG_STRIKE = 40.0
"""Largest :math:`|k|` searched for the tail; past it the strike is :math:`e^{\\mp40}F`."""

MIN_SUM_STRIKES = 3
"""Fewest strikes for a quote sum: :math:`K_0` and at least one more on a side."""

HEADLINE_DAYS = 30.0
"""The constant-maturity tenor on the card (decision 7)."""

_erfc = np.vectorize(math.erfc, otypes=[float])


def _cdf(x: np.ndarray) -> np.ndarray:
    """:math:`\\Phi(x)` through erfc, accurate far into either tail."""
    return 0.5 * _erfc(-np.asarray(x, dtype=float) / math.sqrt(2.0))


def black_call(k: np.ndarray, w: np.ndarray) -> np.ndarray:
    """:math:`c(k, w)`: the Black call in units of :math:`DF` (section 2)."""
    k, root = np.asarray(k, dtype=float), np.sqrt(np.asarray(w, dtype=float))
    d1 = -k / root + root / 2
    return _cdf(d1) - np.exp(k) * _cdf(d1 - root)


def black_put(k: np.ndarray, w: np.ndarray) -> np.ndarray:
    """:math:`p(k, w)`: the Black put in units of :math:`DF` (section 2)."""
    k, root = np.asarray(k, dtype=float), np.sqrt(np.asarray(w, dtype=float))
    d1 = -k / root + root / 2
    return np.exp(k) * _cdf(root - d1) - _cdf(-d1)


def otm_price(k: np.ndarray, w: np.ndarray) -> np.ndarray:
    """:math:`b(k, w)`: the out-of-the-money option, the call for :math:`k \\ge 0`."""
    k = np.asarray(k, dtype=float)
    return np.where(k >= 0, black_call(k, w), black_put(k, w))


def span_total(otm: Callable[[np.ndarray], np.ndarray], low: float, high: float,
               breaks: Sequence[float] = (0.0,)) -> float:
    """:math:`2\\int e^{-k} b(k)\\,dk` over ``[low, high]`` for any out-of-the-money
    price function ``otm`` in units of :math:`DF` (section 2), broken at ``breaks``."""
    return sum(_pieces(otm, low, high, breaks).values())


def _pieces(otm: Callable[[np.ndarray], np.ndarray], low: float, high: float,
            breaks: Sequence[float]) -> dict[tuple[float, float], float]:
    if not (math.isfinite(low) and math.isfinite(high) and low < high):
        raise ValueError("need finite bounds with low < high")
    points = sorted({low, high, *(b for b in breaks if low < b < high)})
    return {(a, b): _simpson(lambda k: 2.0 * np.exp(-k) * otm(k), a, b)
            for a, b in pairwise(points)}


def _simpson(f: Callable[[np.ndarray], np.ndarray], a: float, b: float) -> float:
    x = np.linspace(a, b, INTERVALS + 1)
    y = f(x)
    return float((b - a) / INTERVALS / 3.0
                 * (y[0] + y[-1] + 4.0 * y[1:-1:2].sum() + 2.0 * y[2:-1:2].sum()))


def _z(params: RawSVI, k: np.ndarray) -> np.ndarray:
    root = np.sqrt(params.total_variance(k))
    return np.asarray(k, dtype=float) / root + root / 2


def smile_bounds(params: RawSVI) -> tuple[float, float]:
    """The :math:`k` range where :math:`z` reaches :math:`\\mp` ``TAIL_Z``, by doubling
    :math:`|k|` from one ATM sd (section 2)."""
    step = math.sqrt(float(params.total_variance(0.0)))
    if not step > 0:
        raise ValueError("the smile needs positive ATM total variance")
    high = step
    while float(_z(params, high)) < TAIL_Z and high < MAX_LOG_STRIKE:
        high *= 2.0
    low = -step
    while float(_z(params, low)) > -TAIL_Z and low > -MAX_LOG_STRIKE:
        low *= 2.0
    return low, high


@dataclass(frozen=True)
class SmileVariance:
    """One smile's :math:`K_{var}T`, split into the quoted range and the wings."""

    years: float
    total: float
    quoted: float
    wings: float
    atm_total: float

    @property
    def variance(self) -> float:
        """Annualized :math:`K_{var}`."""
        return self.total / self.years

    @property
    def vol(self) -> float:
        """:math:`\\sqrt{K_{var}}`."""
        return math.sqrt(self.variance)

    @property
    def atm_vol(self) -> float:
        return math.sqrt(self.atm_total / self.years)

    @property
    def extrapolated_share(self) -> float:
        """The share of :math:`K_{var}T` that comes from the extrapolated wings."""
        return self.wings / self.total


def smile_variance(params: RawSVI, years: float, k_min: float, k_max: float) -> SmileVariance:
    """The k-form integral of one slice, with its quoted part (sections 2 and 4)."""
    if not (math.isfinite(years) and years > 0):
        raise ValueError("years must be positive")
    if not (math.isfinite(k_min) and math.isfinite(k_max) and k_min < k_max):
        raise ValueError("the quoted range needs finite k_min < k_max")
    low, high = smile_bounds(params)
    low, high = min(low, k_min), max(high, k_max)
    pieces = _pieces(lambda k: otm_price(k, params.total_variance(k)), low, high,
                     (0.0, k_min, k_max))
    quoted = sum(v for (a, b), v in pieces.items() if k_min <= a and b <= k_max)
    total = sum(pieces.values())
    return SmileVariance(years=years, total=total, quoted=quoted, wings=total - quoted,
                         atm_total=float(params.total_variance(0.0)))


BISECTIONS = 80
"""Halvings of the :math:`k` bracket per :math:`z` in ``normal_average``."""


def normal_average(params: RawSVI) -> float:
    """The z-form, :math:`\\int\\varphi(z)\\,w(k(z))\\,dz` (section 2): the check on the
    k-form. Needs a butterfly-free slice, so :math:`z` rises with :math:`k`."""
    low, high = smile_bounds(params)
    z = np.linspace(-TAIL_Z, TAIL_Z, INTERVALS + 1)
    a, b = np.full_like(z, low), np.full_like(z, high)
    for _ in range(BISECTIONS):
        mid = 0.5 * (a + b)
        above = _z(params, mid) > z
        a, b = np.where(above, a, mid), np.where(above, mid, b)
    y = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi) * params.total_variance(0.5 * (a + b))
    return float(2.0 * TAIL_Z / INTERVALS / 3.0
                 * (y[0] + y[-1] + 4.0 * y[1:-1:2].sum() + 2.0 * y[2:-1:2].sum()))


# ------------------------------------------------------------- the quote sum

@dataclass(frozen=True)
class QuoteSum:
    """One expiry's VIX-style sum (section 3), in total variance.

    ``mid``, ``bid`` and ``ask`` are the sum at each price; ``fit`` is the
    fitted smile's prices summed on the same strikes, when a fit is given.
    ``no_bid`` counts strikes skipped for a missing bid, and ``cut_off``
    the strikes past two missing bids in a row.
    """

    dte: float
    years: float
    forward: float
    discount: float
    k0: float
    strikes: np.ndarray
    mid: float
    bid: float
    ask: float
    fit: float | None
    no_bid: int
    cut_off: int

    @property
    def vol(self) -> float:
        return math.sqrt(max(self.mid, 0.0) / self.years)

    @property
    def vol_band(self) -> tuple[float, float]:
        return (math.sqrt(max(self.bid, 0.0) / self.years),
                math.sqrt(max(self.ask, 0.0) / self.years))

    @property
    def inside(self) -> bool | None:
        """Whether the fitted smile's sum lies inside the quotes' band."""
        return None if self.fit is None else self.bid <= self.fit <= self.ask


def quote_sum(chain: OptionChain, dte: float, params: RawSVI | None = None) -> QuoteSum | None:
    """The sum at one expiry (section 3), or ``None`` when the quotes can't make
    one: no two-sided put at or below the forward, or fewer than
    ``MIN_SUM_STRIKES`` strikes."""
    if dte not in chain.expiries:
        raise ValueError(f"no quotes at {dte} days")
    forward, discount = chain.forward(dte), chain.discount(dte)
    puts = {q.strike: q for q in chain.quotes if q.dte == dte and q.option_type == "put"}
    calls = {q.strike: q for q in chain.quotes if q.dte == dte and q.option_type == "call"}
    two_sided_puts = [k for k, q in puts.items() if k <= forward and q.bid > 0]
    if not two_sided_puts:
        return None
    k0 = max(two_sided_puts)
    below, no_bid_below, cut_below = _walk(sorted((k for k in puts if k < k0), reverse=True), puts)
    above, no_bid_above, cut_above = _walk(sorted(k for k in calls if k > k0), calls)
    strikes = np.array(sorted([*below, k0, *above]))
    if len(strikes) < MIN_SUM_STRIKES:
        return None

    put0, call0 = puts[k0], calls.get(k0)
    shift = discount * (forward - k0)                  # C - P at K0, by parity
    if call0 is not None and call0.bid > 0:
        at_k0 = [(put0.bid + call0.bid) / 2, (put0.mid + call0.mid) / 2, (put0.ask + call0.ask) / 2]
    else:
        at_k0 = [put0.bid + shift / 2, put0.mid + shift / 2, put0.ask + shift / 2]
    rows = np.array([at_k0 if k == k0 else _prices(puts[k] if k < k0 else calls[k])
                     for k in strikes])                 # (n, 3): bid, mid, ask

    spacing = np.empty(len(strikes))
    spacing[1:-1] = (strikes[2:] - strikes[:-2]) / 2
    spacing[0], spacing[-1] = strikes[1] - strikes[0], strikes[-1] - strikes[-2]
    weights = 2.0 / discount * spacing / strikes**2
    ratio = forward / k0
    correction = 2.0 * (ratio - 1.0 - math.log(ratio))
    bid, mid, ask = (float(weights @ rows[:, j]) - correction for j in range(3))

    fit = None
    if params is not None:
        k = np.log(strikes / forward)
        w = params.total_variance(k)
        fitted = np.where(strikes < k0, black_put(k, w),
                          np.where(strikes > k0, black_call(k, w),
                                   (black_put(k, w) + black_call(k, w)) / 2))
        fit = float(weights @ (discount * forward * fitted)) - correction
    return QuoteSum(dte=dte, years=dte / DAYS_PER_YEAR, forward=forward, discount=discount,
                    k0=k0, strikes=strikes, mid=mid, bid=bid, ask=ask, fit=fit,
                    no_bid=no_bid_below + no_bid_above, cut_off=cut_below + cut_above)


def _walk(strikes: list[float], quotes: dict[float, OptionQuote]) -> tuple[list[float], int, int]:
    """Out from :math:`K_0`: skip a strike with no bid, stop at two in a row."""
    kept: list[float] = []
    skipped = run = 0
    for i, k in enumerate(strikes):
        if quotes[k].bid > 0:
            kept.append(k)
            run = 0
            continue
        skipped += 1
        run += 1
        if run == 2:
            return kept, skipped, len(strikes) - i - 1
    return kept, skipped, 0


def _prices(q: OptionQuote) -> list[float]:
    return [q.bid, q.mid, q.ask]


# ------------------------------------------------------------ across expiries

@dataclass(frozen=True)
class ExpiryVariance:
    """One fitted expiry: the smile's :math:`K_{var}T` and the quotes' sum."""

    dte: float
    smile: SmileVariance
    quotes: QuoteSum | None

    @property
    def years(self) -> float:
        return self.smile.years

    @property
    def total(self) -> float:
        return self.smile.total


@dataclass(frozen=True)
class ForwardVariance:
    """Annualized variance between two fitted expiries (section 6)."""

    start_dte: float
    end_dte: float
    variance: float

    @property
    def calendar_ok(self) -> bool:
        return self.variance >= 0


@dataclass(frozen=True)
class TenorVariance:
    """A constant-maturity tenor, interpolated in total variance (section 6).

    ``quote_mid``/``quote_bid``/``quote_ask`` are the quote sums
    interpolated alike, when both bracketing expiries have one.
    """

    days: float
    between: tuple[float, float]
    smile: SmileVariance
    quote_mid: float | None
    quote_bid: float | None
    quote_ask: float | None

    @property
    def vol(self) -> float:
        return self.smile.vol

    @property
    def quote_vol(self) -> float | None:
        return None if self.quote_mid is None else math.sqrt(max(self.quote_mid, 0.0)
                                                              / self.smile.years)


@dataclass(frozen=True)
class VarianceTermStructure:
    """Every fitted expiry's implied variance, and the forward variances between."""

    expiries: tuple[ExpiryVariance, ...]
    forwards: tuple[ForwardVariance, ...]

    @property
    def calendar_free(self) -> bool:
        return all(f.calendar_ok for f in self.forwards)

    def tenor(self, days: float = HEADLINE_DAYS) -> TenorVariance | None:
        """The constant-maturity tenor, or ``None`` outside the fitted expiries
        (it never extrapolates in :math:`T`)."""
        return constant_maturity(self.expiries, days)


def constant_maturity(expiries: Sequence[ExpiryVariance], days: float) -> TenorVariance | None:
    """Interpolate total variance linearly in :math:`T` (section 6)."""
    if not (math.isfinite(days) and days > 0):
        raise ValueError("the tenor must be a positive number of days")
    ordered = sorted(expiries, key=lambda e: e.dte)
    pairs = [(e, e) for e in ordered if e.dte == days]
    pairs += [(a, b) for a, b in pairwise(ordered) if a.dte < days < b.dte]
    if not pairs:
        return None
    front, back = pairs[0]
    lam = 1.0 if back is front else (back.dte - days) / (back.dte - front.dte)

    def mix(a: float, b: float) -> float:
        return lam * a + (1.0 - lam) * b

    smile = SmileVariance(
        years=days / DAYS_PER_YEAR, total=mix(front.smile.total, back.smile.total),
        quoted=mix(front.smile.quoted, back.smile.quoted),
        wings=mix(front.smile.wings, back.smile.wings),
        atm_total=mix(front.smile.atm_total, back.smile.atm_total))
    sums = (front.quotes, back.quotes)
    quoted = all(s is not None for s in sums)
    return TenorVariance(
        days=days, between=(front.dte, back.dte), smile=smile,
        quote_mid=mix(sums[0].mid, sums[1].mid) if quoted else None,
        quote_bid=mix(sums[0].bid, sums[1].bid) if quoted else None,
        quote_ask=mix(sums[0].ask, sums[1].ask) if quoted else None)


def forward_variances(expiries: Sequence[ExpiryVariance]) -> tuple[ForwardVariance, ...]:
    """:math:`(w_2 - w_1)/(T_2 - T_1)` between adjacent expiries (section 6)."""
    ordered = sorted(expiries, key=lambda e: e.dte)
    return tuple(ForwardVariance(a.dte, b.dte, (b.total - a.total) / (b.years - a.years))
                 for a, b in pairwise(ordered))


def implied_variance(fit: SurfaceFit) -> VarianceTermStructure:
    """Every fitted slice's model-free implied variance and quote sum, and the
    forward variances between them."""
    if not fit.slices:
        raise ValueError("no fitted slices")
    expiries = tuple(
        ExpiryVariance(dte=s.dte,
                       smile=smile_variance(s.fit.params, s.years, s.k_min, s.k_max),
                       quotes=quote_sum(fit.chain, s.dte, s.fit.params))
        for s in sorted(fit.slices, key=lambda s: s.dte))
    return VarianceTermStructure(expiries, forward_variances(expiries))
