r"""Activity: what happened in an account, one transaction at a time (TR1, D90).

A **transaction** is one line of a broker's activity history: a trade, a
dividend, a deposit, a split. Any broker's export becomes this shape
(``importers/activity_csv.py``), and everything built on the history (the
replay against positions snapshots, lots, round trips, how well each trade
was timed) reads only this shape.

Conventions
-----------
* ``quantity`` (q) is the signed change in what's held: shares for stocks
  and ETFs, contracts for options. Positive when shares arrive.
* ``amount`` (A) is the signed change in cash, in dollars, net of commission
  and fees. Positive when cash arrives.
* ``price`` is per share as quoted, per *share* for options too (an option's
  multiplier, 100, is applied to it, never folded into it).
* ``commission`` and ``fees`` are dollars, never negative. They are already
  inside ``amount``; they're kept apart so the cost of trading can be
  counted.

Kinds, and what each one requires
---------------------------------
=================================  ============  =========================
kind                               quantity      amount
=================================  ============  =========================
buy, cover, reinvest               q > 0         A = −q·p·m − c − f
sell, short                        q < 0         A = −q·p·m − c − f
dividend, interest, fee, journal   q = 0         any
deposit                            q = 0         A > 0
withdrawal                         q = 0         A < 0
split                              q ≠ 0         A = 0
transfer_in / transfer_out         q > 0 / q < 0 A = 0
expire, assign, exercise           q ≠ 0         any (an option's own row)
other                              any           any
=================================  ============  =========================

Here m is the multiplier (100 for an option, else 1), c the commission and f
the fees. For a buy, q > 0 makes −q·p·m negative: the cash paid. For a
sell, q < 0 makes it positive: the cash received. Commission and fees
lower both, so one formula serves every trade.

**The tolerance** on that equality comes from two roundings. The broker
posts the amount rounded to the cent from the true fill price :math:`p^*`,
and shows the price rounded to the cent or finer, so :math:`|p - p^*| \le
\tfrac12` ¢. Then

.. math::  |A - (-q\,p\,m - c - f)| \le |q|\,m\,|p - p^*| + \tfrac12¢
           \le \tfrac12¢\cdot|q|\,m + \tfrac12¢

and the check allows :math:`\$0.01 + \$0.005\cdot|q|\,m`: half a cent a
share, plus a cent for the amount's rounding and float noise. On a cheap
stock the price's rounding is most of it: 400 shares sold at a true 1.526
post $610.40, but the price column says 1.53, so q·p is $612.00. The gap
is $1.60 and the bound $2.01. A price shown to more decimals is closer to
the truth, so the bound still holds. A column read wrong (a missing multiplier, a
misplaced decimal point) misses by far more.

A deposit or withdrawal is money crossing the account's edge, so its sign
is its meaning: the returns as held (TR6) count it as a flow. A dividend's,
an interest payment's or a fee's sign isn't fixed (a dividend can be
reversed, interest can be charged), so it isn't checked.

A **journal** moves cash between an account's own cash types: a broker that
keeps a short sale's proceeds apart marks the short to market each day,
debiting one type and crediting the other. A day's journals net to zero
within the account, so they change neither its cash nor its P&L; they're
kept so the history is complete.

Reading the same days twice
---------------------------
Two rows can be identical and both real: two fills of one share at the same
price on the same day. So a store can neither skip identical rows nor add
every row it's given. Each transaction has a ``key`` (account, trade date,
kind, symbol, quantity, price, amount), and within a file each copy of a key
gets an **occurrence** number, 1 for the first, 2 for the second, and so
on (``occurrences``). The store keeps one row per (key, occurrence) and
ignores a second insert of the same pair.

Why that's right: where two exports both cover a day, each is complete for
that day, so a key that truly happened n times appears n times in each.
Suppose the store holds c′ copies of a key (occurrences 1 … c′) and a file
brings c (1 … c). Inserting ignores 1 … min(c, c′) and adds the rest, which
leaves max(c, c′). That is n whenever either count is n. A file that ends
part-way through a day can only hold fewer copies of that day's keys, never
more, so the maximum still picks the complete count. Importing a file again
changes nothing.

What it can't catch: a row the broker later corrects, say a changed amount,
has a new key and counts twice. The replay against positions snapshots
(TR2) shows it as a residual on that symbol.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum

from quant_hub.data.positions import OPTION_MULTIPLIER, OptionContract

AMOUNT_TOLERANCE = 0.01       # dollars: the amount's own rounding, and float noise
PER_SHARE_TOLERANCE = 0.005   # dollars a share: a price shown rounded to the cent


class Kind(Enum):
    BUY = "buy"
    SELL = "sell"
    SHORT = "short"                # sell to open
    COVER = "cover"                # buy to close a short
    REINVEST = "reinvest"          # a dividend buying shares
    DIVIDEND = "dividend"
    INTEREST = "interest"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    SPLIT = "split"
    TRANSFER_IN = "transfer_in"    # shares arriving from another account
    TRANSFER_OUT = "transfer_out"
    EXPIRE = "expire"              # an option's own row when it expires,
    ASSIGN = "assign"              # is assigned,
    EXERCISE = "exercise"          # or is exercised
    JOURNAL = "journal"            # cash between an account's own types; nets to zero
    OTHER = "other"                # kept and shown; no effect on P&L


# Shares for cash at a price: the amount must match quantity times price.
TRADES = frozenset({Kind.BUY, Kind.SELL, Kind.SHORT, Kind.COVER, Kind.REINVEST})
# Cash only: no shares move.
CASH_ONLY = frozenset({Kind.DIVIDEND, Kind.INTEREST, Kind.DEPOSIT, Kind.WITHDRAWAL, Kind.FEE,
                       Kind.JOURNAL})
# Shares move with no cash.
NO_CASH = frozenset({Kind.SPLIT, Kind.TRANSFER_IN, Kind.TRANSFER_OUT})
OPTION_EVENTS = frozenset({Kind.EXPIRE, Kind.ASSIGN, Kind.EXERCISE})

_ARRIVE = frozenset({Kind.BUY, Kind.COVER, Kind.REINVEST, Kind.TRANSFER_IN})
_LEAVE = frozenset({Kind.SELL, Kind.SHORT, Kind.TRANSFER_OUT})


_CASH_TYPE = re.compile(r"\((cash|margin|short)\)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Transaction:
    """One line of activity (module docstring for units and rules). An
    invalid one raises ``ValueError`` naming what's wrong."""

    trade_date: date
    kind: Kind
    amount: float
    account: str = ""
    symbol: str = ""
    quantity: float = 0.0
    price: float | None = None
    commission: float = 0.0
    fees: float = 0.0
    settle_date: date | None = None
    description: str = ""
    option: OptionContract | None = None

    def __post_init__(self) -> None:
        problem = self._problem()
        if problem:
            raise ValueError(problem)

    def _problem(self) -> str:
        kind, q, a = self.kind, self.quantity, self.amount
        name = f"{kind.value} {self.symbol}".rstrip()
        for label, value in (("quantity", q), ("amount", a), ("commission", self.commission),
                             ("fees", self.fees)):
            if not math.isfinite(value):
                return f"{name}: {label} must be a number"
        if self.commission < 0 or self.fees < 0:
            return f"{name}: commission and fees can't be negative"
        if self.price is not None and (not math.isfinite(self.price) or self.price <= 0):
            return f"{name}: the price must be positive"
        if self.settle_date is not None and self.settle_date < self.trade_date:
            return f"{name}: it settles before its trade date"
        needs_symbol = kind in TRADES or kind in NO_CASH or kind in OPTION_EVENTS
        if needs_symbol and not self.symbol:
            return f"a {kind.value} needs a symbol"
        if self.option is not None and not self.symbol:
            return "an option needs a symbol"
        if kind in OPTION_EVENTS and self.option is None:
            return f"{name}: only an option can {kind.value}"
        if kind in _ARRIVE and not q > 0:
            return f"{name}: the quantity must be positive (shares arrive)"
        if kind in _LEAVE and not q < 0:
            return f"{name}: the quantity must be negative (shares leave)"
        if (kind is Kind.SPLIT or kind in OPTION_EVENTS) and q == 0:
            return f"{name}: the quantity can't be zero"
        if kind in CASH_ONLY and q != 0:
            return f"{name}: no shares move in a {kind.value}, so the quantity must be zero"
        if kind in NO_CASH and a != 0:
            return f"{name}: no cash moves in a {kind.value}, so the amount must be zero"
        if kind is Kind.DEPOSIT and not a > 0:
            return "a deposit's amount must be positive"
        if kind is Kind.WITHDRAWAL and not a < 0:
            return "a withdrawal's amount must be negative"
        if kind in TRADES:
            if self.price is None:
                return f"{name}: a trade needs its price"
            expected = self.expected_amount
            if abs(a - expected) > self.amount_tolerance:
                return (f"{name}: the amount {a:,.2f} doesn't match quantity × price"
                        f"{' × 100' if self.option else ''} − commission − fees = {expected:,.2f}")
        return ""

    @property
    def cash_type(self) -> str:
        """The account type the row's cash sits in: ``"cash"``, ``"margin"``
        or ``"short"`` (TR6, ``models/held.py`` section 0). A broker that
        says ends the row's wording with it, as Fidelity does ("… (Short)");
        a row that doesn't say is cash."""
        match = _CASH_TYPE.search(self.description)
        return match.group(1).lower() if match else "cash"

    @property
    def multiplier(self) -> float:
        """100 for an option, 1 otherwise."""
        return OPTION_MULTIPLIER if self.option is not None else 1.0

    @property
    def amount_tolerance(self) -> float:
        """How far a trade's amount may sit from −q·p·m − c − f: $0.01 plus
        half a cent a share (module docstring)."""
        return AMOUNT_TOLERANCE + PER_SHARE_TOLERANCE * abs(self.quantity) * self.multiplier

    @property
    def expected_amount(self) -> float:
        """A trade's cash from its own columns, −q·p·m − c − f; NaN without a price."""
        if self.price is None:
            return math.nan
        return -self.quantity * self.price * self.multiplier - self.commission - self.fees

    @property
    def key(self) -> str:
        """What two identical rows share (module docstring): account, trade
        date, kind, symbol, quantity, price and amount, written to fixed
        decimals so the same text always gives the same key."""
        price = "" if self.price is None else _fixed(self.price, 6)
        return "|".join((self.account, self.trade_date.isoformat(), self.kind.value,
                         self.symbol, _fixed(self.quantity, 6), price, _fixed(self.amount, 2)))


@dataclass(frozen=True)
class RowProblem:
    """A row that couldn't be read: its line in the file, and why."""

    line: int
    message: str


@dataclass(frozen=True)
class ActivityFile:
    """One file's transactions in file order, and the rows that failed."""

    transactions: tuple[Transaction, ...]
    source: str                    # the layout it came from: "Generic"
    name: str = ""                 # the file it came from
    problems: tuple[RowProblem, ...] = ()

    @property
    def first_date(self) -> date | None:
        return min((t.trade_date for t in self.transactions), default=None)

    @property
    def last_date(self) -> date | None:
        return max((t.trade_date for t in self.transactions), default=None)

    @property
    def unrecognized(self) -> tuple[str, ...]:
        """The wording of each row read as ``other``, once each, in file
        order: what a broker's layout doesn't map to a kind yet."""
        return tuple(dict.fromkeys(t.description for t in self.transactions
                                   if t.kind is Kind.OTHER))


def occurrences(transactions: Sequence[Transaction]) -> list[int]:
    """Each transaction's occurrence number among those with its key, in
    order, from 1 (module docstring)."""
    seen: Counter[str] = Counter()
    numbers = []
    for transaction in transactions:
        seen[transaction.key] += 1
        numbers.append(seen[transaction.key])
    return numbers


def _fixed(value: float, places: int) -> str:
    """``value`` to ``places`` decimals, with −0 written as 0."""
    return f"{round(value, places) + 0.0:.{places}f}"
