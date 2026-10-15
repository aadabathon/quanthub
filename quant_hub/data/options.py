r"""Option-chain observations: the source-agnostic contract for quoted options.

``OptionChain`` is to options what ``PriceHistory`` is to prices. It is the
one validated shape every consumer (IV inversion, surface fitting, future
backtests) reads, whichever source produced it. Today that is the synthetic
generator in ``models/vol_surface.py``; an IBKR chain adapter in
``broker/`` will produce the same type (D5: domain defines the shape,
infrastructure fills it).

Units: ``dte`` is calendar days to expiry; prices are per share; ``rate``
and ``dividend_yield`` are annualized continuously-compounded decimals.
A ``bid`` of 0 is a legal observation meaning "no bid" (common in far wings);
consumers decide whether to use such quotes.

Carry: a chain states one flat ``rate`` and ``dividend_yield``, and may
override them per expiry with an ``ExpiryCarry`` (that expiry's forward
:math:`F` and discount factor :math:`D`, e.g. implied from put–call parity).
The equivalent continuously-compounded pair

.. math::  r_T = -\frac{\ln D}{T}, \qquad q_T = r_T - \frac{\ln(F/S)}{T}

reproduces :math:`F = S e^{(r_T - q_T)T}` and :math:`D = e^{-r_T T}` exactly,
so every Black–Scholes–Merton consumer that takes :math:`(r, q)` works
unchanged through ``rate_at``/``dividend_yield_at``.

Providers (D34): anything with ``get_chain(symbol, request)`` returning an
``OptionChain`` is an ``OptionChainSource``. ``ChainRequest`` says which
contracts to fetch, and ``select_expiries``/``nearest_strikes``/
``band_strikes`` are the provider-neutral rules for choosing them from what
is listed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class OptionQuote:
    """One quoted European option (per-share bid/ask)."""

    option_type: str
    strike: float
    dte: float
    bid: float
    ask: float
    reference_iv: float | None = None
    """The provider's own implied vol, if it publishes one: a cross-check,
    never an input to the fit."""

    def __post_init__(self) -> None:
        if self.reference_iv is not None and not (
                math.isfinite(self.reference_iv) and self.reference_iv > 0):
            raise ValueError("reference_iv must be positive and finite when given")
        if self.option_type not in ("call", "put"):
            raise ValueError(f"unknown option_type {self.option_type!r}")
        if not math.isfinite(self.strike) or self.strike <= 0:
            raise ValueError("strike must be positive")
        if not math.isfinite(self.dte) or self.dte <= 0:
            raise ValueError("days to expiration must be positive")
        if not (math.isfinite(self.bid) and math.isfinite(self.ask)):
            raise ValueError("bid and ask must be finite")
        if self.bid < 0 or self.ask <= 0 or self.bid > self.ask:
            raise ValueError(f"need 0 <= bid <= ask and ask > 0, got {self.bid}/{self.ask}")

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def half_spread(self) -> float:
        return 0.5 * (self.ask - self.bid)


@dataclass(frozen=True)
class ExpiryCarry:
    """One expiry's forward :math:`F` and discount factor :math:`D`."""

    forward: float
    discount: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.forward) and self.forward > 0):
            raise ValueError("forward must be positive and finite")
        if not (math.isfinite(self.discount) and self.discount > 0):
            raise ValueError("discount factor must be positive and finite")


@dataclass(frozen=True)
class OptionChain:
    """Quotes on one underlying at one moment, plus the carry inputs needed
    to interpret them (spot, rate, dividend yield)."""

    symbol: str
    source: str
    as_of: datetime
    spot: float
    rate: float
    dividend_yield: float
    quotes: tuple[OptionQuote, ...] = field(default_factory=tuple)
    carry: Mapping[float, ExpiryCarry] = field(default_factory=dict)
    """Per-expiry overrides of the flat rate/yield, keyed by ``dte``."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "quotes", tuple(self.quotes))
        object.__setattr__(self, "carry", MappingProxyType(dict(self.carry)))
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if not math.isfinite(self.spot) or self.spot <= 0:
            raise ValueError("spot must be positive")
        if not (math.isfinite(self.rate) and math.isfinite(self.dividend_yield)):
            raise ValueError("rate and dividend yield must be finite")
        if not self.quotes:
            raise ValueError("a chain needs at least one quote")

    @property
    def expiries(self) -> tuple[float, ...]:
        """Distinct expiries (days), ascending."""
        return tuple(sorted({q.dte for q in self.quotes}))

    def forward(self, dte: float) -> float:
        r"""The forward the smile is centred on: the expiry's carry if given,
        else :math:`S e^{(r-q)T}`."""
        if dte in self.carry:
            return self.carry[dte].forward
        return self.spot * math.exp((self.rate - self.dividend_yield) * dte / 365.0)

    def discount(self, dte: float) -> float:
        r""":math:`D(T)`: the expiry's carry if given, else :math:`e^{-rT}`."""
        if dte in self.carry:
            return self.carry[dte].discount
        return math.exp(-self.rate * dte / 365.0)

    def rate_at(self, dte: float) -> float:
        r""":math:`r_T = -\ln D / T` for this expiry (module docstring)."""
        if dte in self.carry:
            return -math.log(self.carry[dte].discount) / (dte / 365.0)
        return self.rate

    def dividend_yield_at(self, dte: float) -> float:
        r""":math:`q_T = r_T - \ln(F/S)/T` for this expiry (module docstring)."""
        if dte in self.carry:
            return self.rate_at(dte) - math.log(self.carry[dte].forward / self.spot) / (dte / 365.0)
        return self.dividend_yield


# ------------------------------------------------------------- acquisition

@dataclass(frozen=True)
class ChainRequest:
    r"""Which contracts a chain provider should fetch, and the fallback carry.

    Expiries: the listed ones nearest ``target_dtes``, at least ``min_dte``
    days out (same-day expiries are listed but useless for a smile). Strikes:
    calls and puts at the ``parity_pairs`` strikes nearest the money (put–call
    parity needs both), then up to ``strikes_per_expiry`` out-of-the-money
    strikes spread over :math:`k \in` ``k_range_sd`` ATM standard deviations,
    using the expiry's ATM vol, or ``vol_guess`` when none is known.
    ``rate``/``dividend_yield`` are the chain's flat carry until per-expiry
    carry replaces them.
    """

    target_dtes: tuple[float, ...] = (7, 30, 60, 91, 182, 365)
    min_dte: float = 2.0
    strikes_per_expiry: int = 21
    parity_pairs: int = 5
    k_range_sd: tuple[float, float] = (-2.5, 1.5)
    vol_guess: float = 0.25
    rate: float = 0.04
    dividend_yield: float = 0.0

    def __post_init__(self) -> None:
        if not self.target_dtes or any(not math.isfinite(d) or d <= 0 for d in self.target_dtes):
            raise ValueError("target expiries must be positive day counts")
        if not (math.isfinite(self.min_dte) and self.min_dte > 0):
            raise ValueError("min_dte must be positive")
        if self.strikes_per_expiry < 2 or self.parity_pairs < 0:
            raise ValueError("need at least two strikes per expiry and parity_pairs >= 0")
        low, high = self.k_range_sd
        if not low < 0 < high:
            raise ValueError("k_range_sd must straddle zero")
        if not (math.isfinite(self.vol_guess) and self.vol_guess > 0):
            raise ValueError("vol_guess must be positive")
        if not (math.isfinite(self.rate) and math.isfinite(self.dividend_yield)):
            raise ValueError("rate and dividend yield must be finite")


def select_expiries(listed_dtes: Iterable[float], request: ChainRequest) -> list[float]:
    """For each target, the listed expiry nearest it (ties to the earlier),
    at least ``min_dte`` out. Ascending, without repeats."""
    candidates = sorted(d for d in listed_dtes if d >= request.min_dte)
    if not candidates:
        return []
    return sorted({min(candidates, key=lambda d: (abs(d - target), d))
                   for target in request.target_dtes})


def nearest_strikes(listed: Iterable[float], center: float, count: int) -> list[float]:
    """The ``count`` listed strikes nearest ``center``, ascending."""
    return sorted(sorted(set(listed), key=lambda k: (abs(k - center), k))[:count])


def band_strikes(listed: Iterable[float], forward: float, vol: float, years: float,
                 request: ChainRequest) -> list[float]:
    r"""Up to ``strikes_per_expiry`` listed strikes inside
    :math:`[F e^{a\sigma\sqrt{T}}, F e^{b\sigma\sqrt{T}}]` (``k_range_sd`` =
    (a, b)), each the nearest to an even grid in log-moneyness, so the wings
    are covered as evenly as the listing allows."""
    if not (forward > 0 and vol > 0 and years > 0):
        raise ValueError("forward, vol and time to expiry must be positive")
    sd = vol * math.sqrt(years)
    low, high = (forward * math.exp(z * sd) for z in request.k_range_sd)
    inside = sorted(k for k in set(listed) if low <= k <= high)
    if not inside:
        return []
    count = request.strikes_per_expiry
    grid = (forward * math.exp((request.k_range_sd[0] + (request.k_range_sd[1]
                                                         - request.k_range_sd[0]) * i
                                / (count - 1)) * sd) for i in range(count))
    return sorted({min(inside, key=lambda k, g=g: abs(k - g)) for g in grid})


@runtime_checkable
class OptionChainSource(Protocol):
    """Anything that can serve a quoted chain for an underlying (D34)."""

    name: str

    def get_chain(self, symbol: str, request: ChainRequest) -> OptionChain:
        """Quote the requested contracts at one moment. Raises on failure."""
        ...
