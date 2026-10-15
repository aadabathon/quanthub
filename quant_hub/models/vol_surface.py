r"""Chain → implied vols → per-expiry SVI fits → arbitrage diagnostics.

The pipeline only ever sees an ``OptionChain`` (``data/options.py``), so the
same code will fit a real IBKR chain once one exists. ``synthetic_chain``
builds one from a known ``SSVISurface``, which is what lets the lab score
the fit against the truth.

Synthetic quotes
----------------
For each expiry, strikes are placed evenly in log-moneyness over
:math:`k \in [-2.5, +1.5]\sqrt{\theta(T)}`, i.e. in ATM standard deviations,
skewed toward puts like listed equity chains. Each quote is the
out-of-the-money option (puts below the forward, calls at or above), since
that is where the liquid, informative prices are. The true price
:math:`p` comes from Black-Scholes at the surface's vol. The quote is

.. math::  h = \max(h_{min}, \tfrac{s}{2}p), \qquad
           \text{mid} = p + \nu h Z, \qquad \text{bid/ask} = \text{mid} \mp h,

with :math:`s` the relative spread, :math:`h_{min}` half a one-cent tick,
:math:`\nu` the noise level in half-spreads, and :math:`Z \sim N(0,1)`. A
negative bid is floored at zero ("no bid"), as in real far wings.

Implied vols and weights
------------------------
Each usable quote's mid is inverted with the bracketed-Newton solver
(``black_scholes.implied_volatility``). Quotes with no bid, or whose mid is
outside the no-arbitrage bounds, are excluded and counted. The fit weights
propagate the quote's half-spread :math:`h` into total variance by the delta
method: :math:`\delta\sigma \approx h/\nu_{BS}` (raw vega), and
:math:`w = \sigma^2T` gives

.. math::  \delta w = 2\sigma T\,\delta\sigma = \frac{2\sigma T h}{\nu_{BS}}.

So wide or low-vega quotes (deep wings) count less, exactly as their
information content says they should.

Diagnostics
-----------
* **butterfly**: the minimum of :math:`g(k)` (``svi.RawSVI.butterfly_g``)
  over each slice's quoted range widened by 50%. Negative means arbitrage.
* **calendar**: for adjacent expiries,
  :math:`\min_k [w_{T_{i+1}}(k) - w_{T_i}(k)]` over their shared quoted
  range. Negative means a calendar spread with negative cost, i.e.
  arbitrage.

The per-expiry fit is independent by design (no cross-expiry smoothing), so
these checks are genuine tests of the fitted surface, not properties it has
by construction.

Surface mesh (the 3D view)
--------------------------
The fit gives smiles only at the quoted expiries :math:`T_1 < \dots < T_N`.
``surface_mesh`` fills the surface between them by interpolating total
variance linearly in :math:`T` at fixed log-moneyness :math:`k`:

.. math::  w(k, T) = \lambda\,w_i(k) + (1 - \lambda)\,w_{i+1}(k), \qquad
           \lambda = \frac{T_{i+1} - T}{T_{i+1} - T_i},
           \quad T \in [T_i, T_{i+1}].

It never extrapolates in :math:`T`: the mesh spans :math:`[T_1, T_N]`.

*Calendar.* :math:`\partial_T w(k, T) = (w_{i+1}(k) - w_i(k))/(T_{i+1} - T_i)`,
so between two expiries :math:`w` rises with :math:`T` exactly where their
fitted smiles don't cross. The interpolation adds no calendar arbitrage of
its own. At :math:`k = 0` it holds the forward variance constant between
expiries. The mesh reports :math:`\min [w_{i+1}(k) - w_i(k)]` over the
:math:`k` it draws, a wider range than the quoted-range check above.

*Butterfly.* :math:`g` is not linear in :math:`w` (it has
:math:`w'^2/w`), so a mix of two butterfly-free smiles need not be
butterfly-free. Each mesh row's :math:`w`, :math:`w'` and :math:`w''` are
the same mixes of the two slices' own derivatives, so the mesh evaluates
:math:`g` on every row (``svi.butterfly_g``) and reports its minimum.

*Axes.* As in ``surface_grid``, columns are standardized moneyness
:math:`z = k/\sqrt{\theta(T)}`, now with :math:`\theta(T) = w(0, T)`, so a
row at :math:`T` is evaluated at :math:`k = z\sqrt{\theta(T)}`. Implied
vol is :math:`\sqrt{w/T}`. The :math:`z` range is the one quoted at every
expiry. Between expiries a row's :math:`k` can still lie past the
*nearer* expiry's quotes, which span fewer of the later expiry's standard
deviations. There the nearer slice's SVI wing (linear in :math:`k`,
Lee-bounded) is extrapolated, and the mesh's ``extrapolated`` mask marks
those points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from quant_hub.data.options import OptionChain, OptionQuote
from quant_hub.models.black_scholes import greeks_array, implied_volatility, price_array
from quant_hub.models.svi import MIN_SLICE_QUOTES, SSVISurface, SVIFit, butterfly_g, fit_raw_svi

MIN_HALF_SPREAD = 0.005
"""Half of a one-cent tick: no quote is tighter than this."""

K_RANGE_SD = (-2.5, 1.5)
"""Synthetic strike range in ATM standard deviations (put-skewed, like equities)."""


@dataclass(frozen=True)
class SyntheticChainSpec:
    """Everything that defines one synthetic chain draw."""

    spot: float = 100.0
    rate: float = 0.04
    dividend_yield: float = 0.0
    expiries: tuple[float, ...] = (7, 30, 60, 91, 182, 365)
    strikes_per_expiry: int = 21
    relative_spread: float = 0.04
    noise: float = 0.5
    seed: int | None = 7

    def __post_init__(self) -> None:
        if not self.expiries or any(not math.isfinite(d) or d <= 0 for d in self.expiries):
            raise ValueError("expiries must be positive day counts")
        if self.strikes_per_expiry < MIN_SLICE_QUOTES:
            raise ValueError(f"need at least {MIN_SLICE_QUOTES} strikes per expiry")
        if not 0 <= self.relative_spread < 1:
            raise ValueError("relative spread must be in [0, 1)")
        if self.noise < 0:
            raise ValueError("noise must be non-negative")


@dataclass(frozen=True)
class ChainIVs:
    """Per-quote arrays (one entry per quote in the chain, same order)."""

    dte: np.ndarray
    strike: np.ndarray
    k: np.ndarray
    is_call: np.ndarray
    mid: np.ndarray
    iv: np.ndarray
    iv_bid: np.ndarray
    iv_ask: np.ndarray
    variance_uncertainty: np.ndarray
    usable: np.ndarray
    excluded_no_bid: int
    excluded_out_of_bounds: int


@dataclass(frozen=True)
class SliceFit:
    dte: float
    years: float
    forward: float
    fit: SVIFit
    k_min: float
    k_max: float
    min_butterfly_g: float

    @property
    def atm_vol(self) -> float:
        return float(self.fit.params.implied_vol(0.0, self.years))


@dataclass(frozen=True)
class SurfaceFit:
    chain: OptionChain
    ivs: ChainIVs
    slices: tuple[SliceFit, ...]
    skipped_expiries: tuple[float, ...]
    calendar_gaps: tuple[float, ...]
    """``min_k [w_{i+1} - w_i]`` for each adjacent pair of fitted slices."""

    @property
    def butterfly_free(self) -> bool:
        return all(s.min_butterfly_g >= 0 for s in self.slices)

    @property
    def calendar_free(self) -> bool:
        return all(gap >= 0 for gap in self.calendar_gaps)


def synthetic_chain(surface: SSVISurface, spec: SyntheticChainSpec,
                    as_of: datetime | None = None) -> OptionChain:
    """Quote an OTM chain off ``surface`` with spread and mid noise (module docstring)."""
    rng = np.random.default_rng(spec.seed)
    quotes: list[OptionQuote] = []
    for dte in sorted(spec.expiries):
        years = dte / 365.0
        forward = spec.spot * math.exp((spec.rate - spec.dividend_yield) * years)
        sd = math.sqrt(surface.atm_total_variance(years))
        k = np.linspace(K_RANGE_SD[0] * sd, K_RANGE_SD[1] * sd, spec.strikes_per_expiry)
        strikes = np.round(forward * np.exp(k), 2)
        vols = surface.slice(years).implied_vol(np.log(strikes / forward), years)
        for strike, vol in zip(strikes, vols):
            option_type = "call" if strike >= forward else "put"
            true_price = float(price_array(spec.spot, strike, dte, spec.rate, vol, option_type,
                                           spec.dividend_yield))
            half = max(MIN_HALF_SPREAD, 0.5 * spec.relative_spread * true_price)
            mid = true_price + spec.noise * half * float(rng.standard_normal())
            bid, ask = max(mid - half, 0.0), max(mid + half, 2 * MIN_HALF_SPREAD)
            quotes.append(OptionQuote(option_type, float(strike), float(dte), bid, ask))
    return OptionChain(
        symbol="SYNTH", source="synthetic SSVI", as_of=as_of or datetime.now(),
        spot=spec.spot, rate=spec.rate, dividend_yield=spec.dividend_yield, quotes=tuple(quotes),
    )


def chain_implied_vols(chain: OptionChain) -> ChainIVs:
    """Invert every quote's mid (and bid/ask, for display) to implied vol."""
    n = len(chain.quotes)
    out = {name: np.full(n, np.nan) for name in ("iv", "iv_bid", "iv_ask", "dw")}
    usable = np.zeros(n, dtype=bool)
    no_bid = out_of_bounds = 0
    dte = np.array([q.dte for q in chain.quotes])
    strike = np.array([q.strike for q in chain.quotes])
    forwards = np.array([chain.forward(q.dte) for q in chain.quotes])

    def solve(price: float, q: OptionQuote) -> float:
        try:
            return implied_volatility(price, chain.spot, q.strike, q.dte, chain.rate,
                                      q.option_type, chain.dividend_yield)
        except ValueError:
            return math.nan

    for i, q in enumerate(chain.quotes):
        out["iv_ask"][i] = solve(q.ask, q)
        if q.bid <= 0:
            no_bid += 1
            continue
        out["iv_bid"][i] = solve(q.bid, q)
        iv = solve(q.mid, q)
        if math.isnan(iv):
            out_of_bounds += 1
            continue
        raw_vega = 100.0 * float(greeks_array(chain.spot, q.strike, q.dte, chain.rate, iv,
                                              q.option_type, chain.dividend_yield).vega)
        if raw_vega <= 0:
            out_of_bounds += 1
            continue
        out["iv"][i] = iv
        out["dw"][i] = 2.0 * iv * (q.dte / 365.0) * max(q.half_spread, MIN_HALF_SPREAD) / raw_vega
        usable[i] = True

    return ChainIVs(
        dte=dte, strike=strike, k=np.log(strike / forwards),
        is_call=np.array([q.option_type == "call" for q in chain.quotes]),
        mid=np.array([q.mid for q in chain.quotes]), iv=out["iv"], iv_bid=out["iv_bid"],
        iv_ask=out["iv_ask"], variance_uncertainty=out["dw"], usable=usable,
        excluded_no_bid=no_bid, excluded_out_of_bounds=out_of_bounds,
    )


def fit_surface(chain: OptionChain) -> SurfaceFit:
    """Fit raw SVI independently per expiry, then run the arbitrage checks."""
    ivs = chain_implied_vols(chain)
    slices: list[SliceFit] = []
    skipped: list[float] = []
    for dte in chain.expiries:
        mask = ivs.usable & (ivs.dte == dte)
        if mask.sum() < MIN_SLICE_QUOTES:
            skipped.append(dte)
            continue
        years = dte / 365.0
        k = ivs.k[mask]
        fit = fit_raw_svi(k, ivs.iv[mask] ** 2 * years, ivs.variance_uncertainty[mask], years)
        k_min, k_max = float(k.min()), float(k.max())
        pad = 0.25 * (k_max - k_min)
        grid = np.linspace(k_min - pad, k_max + pad, 201)
        slices.append(SliceFit(
            dte=dte, years=years, forward=chain.forward(dte), fit=fit, k_min=k_min, k_max=k_max,
            min_butterfly_g=float(fit.params.butterfly_g(grid).min()),
        ))
    gaps = tuple(_calendar_gap(a, b) for a, b in zip(slices, slices[1:]))
    return SurfaceFit(chain, ivs, tuple(slices), tuple(skipped), gaps)


def _calendar_gap(front: SliceFit, back: SliceFit) -> float:
    low, high = max(front.k_min, back.k_min), min(front.k_max, back.k_max)
    if low >= high:
        return math.inf  # no shared strikes: nothing to compare
    grid = np.linspace(low, high, 201)
    return float(np.min(back.fit.params.total_variance(grid) - front.fit.params.total_variance(grid)))


def surface_grid(fit: SurfaceFit, points: int = 61) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r"""``(z, dte, iv)`` of the fitted surface for a heatmap.

    Columns are *standardized* moneyness :math:`z = k/\sqrt{\theta(T)}` (ATM
    standard deviations, using each slice's fitted ATM total variance).
    Raw :math:`k` ranges scale like :math:`\sqrt{T}`, so a 7-day smile spans
    a sliver of a 1-year one. In :math:`z` every expiry covers a comparable
    range. Only the :math:`z` range quoted at every expiry is drawn, so
    nothing is extrapolated past its data.
    """
    if not fit.slices:
        raise ValueError("no fitted slices")
    sds = [math.sqrt(float(s.fit.params.total_variance(0.0))) for s in fit.slices]
    low = max(s.k_min / sd for s, sd in zip(fit.slices, sds))
    high = min(s.k_max / sd for s, sd in zip(fit.slices, sds))
    z = np.linspace(low, high, points)
    dte = np.array([s.dte for s in fit.slices])
    iv = np.vstack([s.fit.params.implied_vol(z * sd, s.years) for s, sd in zip(fit.slices, sds)])
    return z, dte, iv


@dataclass(frozen=True)
class SmileCurves:
    """Plot-ready arrays for one expiry: observed quotes and model curves."""

    dte: float
    k_grid: np.ndarray
    fitted_iv: np.ndarray | None
    true_iv: np.ndarray | None
    quote_k: np.ndarray
    quote_iv: np.ndarray
    quote_iv_bid: np.ndarray
    quote_iv_ask: np.ndarray


def smile_curves(fit: SurfaceFit, truth: SSVISurface | None = None,
                 points: int = 121) -> list[SmileCurves]:
    """Per-expiry quotes (usable only) plus fitted and, if known, true smiles.

    Curves span the expiry's quoted k-range widened by 10% on each side.
    """
    fitted = {s.dte: s for s in fit.slices}
    ivs = fit.ivs
    curves = []
    for dte in fit.chain.expiries:
        mask = ivs.usable & (ivs.dte == dte)
        if not mask.any():
            continue
        years = dte / 365.0
        k_lo, k_hi = float(ivs.k[mask].min()), float(ivs.k[mask].max())
        pad = 0.1 * (k_hi - k_lo)
        grid = np.linspace(k_lo - pad, k_hi + pad, points)
        slice_fit = fitted.get(dte)
        curves.append(SmileCurves(
            dte=dte, k_grid=grid,
            fitted_iv=slice_fit.fit.params.implied_vol(grid, years) if slice_fit else None,
            true_iv=truth.slice(years).implied_vol(grid, years) if truth else None,
            quote_k=ivs.k[mask], quote_iv=ivs.iv[mask],
            quote_iv_bid=ivs.iv_bid[mask], quote_iv_ask=ivs.iv_ask[mask],
        ))
    return curves


MESH_ROWS_BETWEEN = 8
"""Interpolated rows between adjacent fitted expiries in ``surface_mesh``."""

_EDGE = 1e-12
"""Tolerance on the quoted-range edges, so rounding at a node isn't extrapolation."""


@dataclass(frozen=True)
class SurfaceMesh:
    """The fitted surface on a (z, T) grid with its quotes and checks (module docstring).

    Rows are days to expiry ``dte``; every fitted expiry is a row, and
    ``node_rows`` lists which. Columns are standardized moneyness ``z``.
    ``true_iv`` is the known surface at the same (k, T) points, if there is
    one. The quote arrays hold the usable quotes at fitted expiries, placed
    by their own expiry's :math:`\theta`; a bid or ask that doesn't invert
    is NaN.
    """

    z: np.ndarray                  # (n_z,)
    dte: np.ndarray                # (n_t,)
    iv: np.ndarray                 # (n_t, n_z)
    true_iv: np.ndarray | None     # (n_t, n_z)
    extrapolated: np.ndarray       # (n_t, n_z) bool
    node_rows: np.ndarray          # (n_slices,) row of each fitted expiry
    min_butterfly_g: float
    min_calendar_gap: float
    quote_z: np.ndarray
    quote_dte: np.ndarray
    quote_iv: np.ndarray
    quote_iv_bid: np.ndarray
    quote_iv_ask: np.ndarray


def surface_mesh(fit: SurfaceFit, truth: SSVISurface | None = None, points: int = 61,
                 rows_between: int = MESH_ROWS_BETWEEN) -> SurfaceMesh:
    """Interpolate the fitted slices into a mesh for the 3D view (module docstring)."""
    slices = sorted(fit.slices, key=lambda s: s.dte)
    if len(slices) < 2:
        raise ValueError("a surface needs at least two fitted expiries")
    if points < 2 or rows_between < 0:
        raise ValueError("need at least two columns and a non-negative row count")
    params = [s.fit.params for s in slices]
    theta = np.array([float(p.total_variance(0.0)) for p in params])
    low = max(s.k_min / math.sqrt(t) for s, t in zip(slices, theta))
    high = min(s.k_max / math.sqrt(t) for s, t in zip(slices, theta))
    if not low < high:
        raise ValueError("the fitted expiries share no quoted moneyness range")
    z = np.linspace(low, high, points)

    # Rows: each fitted expiry, then ``rows_between`` evenly spaced between it
    # and the next; row r lies in interval ``intervals[r]``.
    days: list[float] = []
    intervals: list[int] = []
    node_rows: list[int] = []
    for i in range(len(slices) - 1):
        node_rows.append(len(days))
        for d in np.linspace(slices[i].dte, slices[i + 1].dte, rows_between + 2)[:-1]:
            days.append(float(d))
            intervals.append(i)
    node_rows.append(len(days))
    days.append(slices[-1].dte)
    intervals.append(len(slices) - 2)

    n_rows = len(days)
    iv = np.empty((n_rows, points))
    true_iv = np.empty((n_rows, points)) if truth is not None else None
    extrapolated = np.zeros((n_rows, points), dtype=bool)
    min_g = min_gap = math.inf
    for row, (d, i) in enumerate(zip(days, intervals)):
        front, back = slices[i], slices[i + 1]
        years = d / 365.0
        lam = (back.years - years) / (back.years - front.years)
        k = z * math.sqrt(lam * theta[i] + (1 - lam) * theta[i + 1])   # z·√θ(T)
        w = lam * params[i].total_variance(k) + (1 - lam) * params[i + 1].total_variance(k)
        iv[row] = np.sqrt(w / years)
        for weight, s in ((lam, front), (1 - lam, back)):
            if weight > 0:
                extrapolated[row] |= (k < s.k_min - _EDGE) | (k > s.k_max + _EDGE)
        if true_iv is not None:
            true_iv[row] = truth.slice(years).implied_vol(k, years)

        # Checks on a dense k grid across the row: g of the mixed smile, and
        # the two slices' gap (the sign of the row's calendar slope).
        dense = np.linspace(k[0], k[-1], 201)
        w_front, w_back = params[i].total_variance(dense), params[i + 1].total_variance(dense)
        (f1, f2), (b1, b2) = params[i].derivatives(dense), params[i + 1].derivatives(dense)
        g = butterfly_g(dense, lam * w_front + (1 - lam) * w_back,
                        lam * f1 + (1 - lam) * b1, lam * f2 + (1 - lam) * b2)
        min_g = min(min_g, float(g.min()))
        min_gap = min(min_gap, float(np.min(w_back - w_front)))

    ivs = fit.ivs
    root_theta = {s.dte: math.sqrt(t) for s, t in zip(slices, theta)}
    quoted = ivs.usable & np.isin(ivs.dte, list(root_theta))
    return SurfaceMesh(
        z=z, dte=np.array(days), iv=iv, true_iv=true_iv, extrapolated=extrapolated,
        node_rows=np.array(node_rows), min_butterfly_g=min_g, min_calendar_gap=min_gap,
        quote_z=ivs.k[quoted] / np.array([root_theta[d] for d in ivs.dte[quoted]]),
        quote_dte=ivs.dte[quoted], quote_iv=ivs.iv[quoted],
        quote_iv_bid=ivs.iv_bid[quoted], quote_iv_ask=ivs.iv_ask[quoted],
    )
