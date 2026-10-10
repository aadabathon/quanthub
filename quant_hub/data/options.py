"""Option-chain observations: the source-agnostic contract for quoted options.

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
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class OptionQuote:
    """One quoted European option (per-share bid/ask)."""

    option_type: str
    strike: float
    dte: float
    bid: float
    ask: float

    def __post_init__(self) -> None:
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "quotes", tuple(self.quotes))
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
        r""":math:`F = S e^{(r-q)T}`, the forward the smile is centred on."""
        return self.spot * math.exp((self.rate - self.dividend_yield) * dte / 365.0)
