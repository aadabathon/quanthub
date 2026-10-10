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
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from quant_hub.data.options import OptionChain, OptionQuote
from quant_hub.models.black_scholes import greeks_array, implied_volatility, price_array
from quant_hub.models.svi import MIN_SLICE_QUOTES, SSVISurface, SVIFit, fit_raw_svi

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
