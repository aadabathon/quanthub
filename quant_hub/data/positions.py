"""Positions: what an account holds, broker-agnostic (P2, D33, D34).

A ``PositionSnapshot`` is one import of an account's holdings at a moment
(``as_of``): stocks and ETFs as share counts, options as contracts, and
cash (money-market funds included). Any broker's export becomes this shape
(``importers/positions_csv.py``); the risk model (``models/portfolio.py``)
reads only this shape.

Conventions:

* ``quantity``: shares for stocks and ETFs, contracts for options
  (negative when short), dollars for cash.
* ``price``: the last price per share (per *share* for options too, as
  quoted), when the export carries one. ``value`` is the position's market
  value in dollars, when the export carries one; an import computes it from
  quantity and price otherwise.
* Options are described by their contract (underlying, call or put, strike,
  expiry) and a multiplier, 100 for US equity options.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

OPTION_MULTIPLIER = 100.0


class Holding(Enum):
    EQUITY = "equity"      # stocks and ETFs: priced and risked the same way
    OPTION = "option"
    CASH = "cash"          # cash and money-market funds: no price risk


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    option_type: str       # "call" or "put"
    strike: float
    expiry: date

    def __post_init__(self) -> None:
        if self.option_type not in ("call", "put"):
            raise ValueError(f"option type must be call or put, not {self.option_type!r}")
        if not math.isfinite(self.strike) or self.strike <= 0:
            raise ValueError("strike must be positive")
        if not self.underlying:
            raise ValueError("an option needs an underlying")

    def days_to_expiry(self, on: date) -> float:
        """Calendar days from ``on`` to expiry (the Options Lab's unit);
        zero or less once expired."""
        return float((self.expiry - on).days)


@dataclass(frozen=True)
class Position:
    """One line of a snapshot (module docstring for units)."""

    symbol: str
    kind: Holding
    quantity: float
    account: str = ""
    price: float | None = None
    value: float | None = None
    cost_basis: float | None = None
    description: str = ""
    option: OptionContract | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("a position needs a symbol")
        if not math.isfinite(self.quantity):
            raise ValueError(f"{self.symbol}: quantity must be finite")
        if (self.kind is Holding.OPTION) != (self.option is not None):
            raise ValueError(f"{self.symbol}: an option position needs its contract, and "
                             "only options have one")
        if self.price is not None and (not math.isfinite(self.price) or self.price < 0):
            raise ValueError(f"{self.symbol}: price must be finite and not negative")

    @property
    def market_value(self) -> float | None:
        """Dollars: the export's value, else quantity times price."""
        if self.value is not None:
            return self.value
        if self.kind is Holding.CASH:
            return self.quantity
        if self.price is None:
            return None
        multiplier = OPTION_MULTIPLIER if self.kind is Holding.OPTION else 1.0
        return self.quantity * self.price * multiplier


@dataclass(frozen=True)
class PositionSnapshot:
    """An account's holdings as of one moment, as imported."""

    positions: tuple[Position, ...]
    as_of: datetime
    source: str                    # the layout it came from: "Fidelity", "Generic"
    name: str = ""                 # the file it came from
    accounts: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if not self.positions:
            raise ValueError("a snapshot needs at least one position")
        if not self.accounts:
            accounts = tuple(dict.fromkeys(p.account for p in self.positions if p.account))
            object.__setattr__(self, "accounts", accounts)

    def for_account(self, account: str | None) -> tuple[Position, ...]:
        """One account's positions; ``None`` for all of them."""
        if account is None:
            return self.positions
        return tuple(p for p in self.positions if p.account == account)
