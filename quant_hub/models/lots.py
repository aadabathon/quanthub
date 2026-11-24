r"""Lots, round trips and realized P&L from a transaction history (TR3, D94).

The history says what was traded; this says what each trade did. Matching
every sale to the purchase it closed gives each piece of a position its
real entry, exit, holding period and profit. Everything that grades a trade
later (how well its entry and exit were timed, what the market did around
it) works on these pieces.

Units and cash
--------------
A **unit** is a share, or a contract for an option. Every trade's **cash
per unit** is

.. math::  c = A / |q|

what one unit paid (negative) or brought in (positive), net of commission
and fees, with an option's multiplier already inside the amount. Using the
amount, not the quoted price, puts every cost into the basis and every fee
into the proceeds.

Lots, first in, first out
-------------------------
For each account and symbol the open **lots** form a queue, oldest first;
they all point the same way (long, or short). A trade against them closes
the oldest first. A **piece** of m units from a lot opened at cash per unit
:math:`c_\ell`, closed by a trade at cash per unit c, realizes

.. math::  \text{pnl} = m\,(c_\ell + c)

For a long piece, :math:`c_\ell < 0` (paid) and c > 0 (received), so this is
m times the difference. For a short piece the signs swap. An expiry has
A = 0, so c = 0: a long option that expires loses what it cost, and a short
one keeps its premium. Summed over the pieces of one sale of :math:`|q|`
units, the realized P&L is :math:`A + \sum m\,c_\ell`. Whatever's left of a
trade once the queue is empty opens a new lot the other way.

- **A split** scales the open lots instead of opening one: with
  :math:`r = (N + q)/N`, each lot's units times r, its cash per unit over r.
  The basis doesn't change, and no zero-cost lot appears.
- **A transfer** moves shares with no cash: a lot transferred in has an
  unknown cost, a piece transferred out has no proceeds, and either way
  that piece's P&L is unknown rather than made up.
- **A symbol held as cash** (a money-market fund) is cash, not a position.

**Within a day** the history has no times, and a broker may list a day's
rows newest first. So a day's opening trades (buys, short sales,
reinvestments, transfers in) are taken before its closing ones. A day
trade is then a long round trip, not a spurious short, and FIFO pieces
don't change unless a position went flat and reopened within the day,
which this can't see.

Round trips
-----------
A **round trip** runs from flat to flat for one account and symbol, and a
trade that crosses zero closes one and opens the next. Its realized P&L is
the sum of its pieces. Income for the symbol (dividends received or
charged, fees named for it) is kept **beside** the P&L, not in it: paid
while the trip is open, or after it closes until the next one opens (a
dividend can be paid after the shares are sold).

Cost basis against the broker's
-------------------------------
At a snapshot the open long lots' cost, :math:`\sum m\,(-c_\ell)`, is the
**FIFO basis**. The **average-cost basis** instead removes the average cost
per unit with each sale. Compared with the snapshot's cost-basis column,
whichever matches says which method the broker uses. A holding that
matches neither points at something the history can't see: a wash sale
(the broker adds a disallowed loss to the replacement's basis), shares
transferred in, or a corporate action. Trades on the snapshot's own day are
placed before or after it, whichever matches its units, as in the replay
(``models/ledger.py``).

This is economic P&L, not tax: no wash-sale adjustments.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from quant_hub.data.activity import Kind, Transaction
from quant_hub.data.positions import Holding, PositionSnapshot

EPS = 1e-9
BASIS_TOLERANCE = 0.05          # dollars, or 0.05% of the basis if more

# Taken first within a day, so a day trade opens before it closes.
_OPENERS = frozenset({Kind.BUY, Kind.SHORT, Kind.REINVEST, Kind.TRANSFER_IN})
_INCOME = frozenset({Kind.DIVIDEND, Kind.FEE, Kind.INTEREST})
_IGNORED = frozenset({Kind.OTHER, Kind.JOURNAL, Kind.DEPOSIT, Kind.WITHDRAWAL})


@dataclass(frozen=True)
class Piece:
    """Units opened by one trade and closed by another (module docstring)."""

    account: str
    symbol: str
    side: str                      # "long" or "short"
    units: float                   # always positive
    opened: date
    closed: date
    open_cash: float | None        # cash per unit when opened; None if unknown
    close_cash: float | None       # cash per unit when closed; None for a transfer out
    open_fill: float | None        # the quoted prices, for timing later
    close_fill: float | None
    option: bool = False

    @property
    def pnl(self) -> float | None:
        if self.open_cash is None or self.close_cash is None:
            return None
        return self.units * (self.open_cash + self.close_cash)

    @property
    def days_held(self) -> int:
        return (self.closed - self.opened).days


@dataclass(frozen=True)
class RoundTrip:
    """Flat to flat for one account and symbol (module docstring)."""

    account: str
    symbol: str
    side: str
    opened: date
    closed: date | None            # None while still open
    pieces: tuple[Piece, ...]
    invested: float                # cash put up to open: paid for longs, received for shorts
    peak_units: float
    open_units: float              # still held (0 once closed)
    income: float = 0.0            # dividends and fees beside the P&L
    option: bool = False

    @property
    def realized(self) -> float | None:
        """The pieces' P&L; None if any piece's cost is unknown."""
        values = [p.pnl for p in self.pieces]
        return None if any(v is None for v in values) else sum(values)

    @property
    def return_on_invested(self) -> float | None:
        realized = self.realized
        if realized is None or self.closed is None or self.invested <= 0:
            return None
        return realized / self.invested


@dataclass(frozen=True)
class BasisCheck:
    """One holding's basis at a snapshot: FIFO's, average cost's, the broker's."""

    snapshot: str
    account: str                   # "" when compared by symbol across accounts
    symbol: str
    units: float
    fifo: float | None
    average: float | None
    broker: float

    def _matches(self, value: float | None) -> bool:
        tolerance = max(BASIS_TOLERANCE, 5e-4 * abs(self.broker))
        return value is not None and abs(value - self.broker) <= tolerance

    @property
    def fifo_matches(self) -> bool:
        return self._matches(self.fifo)

    @property
    def average_matches(self) -> bool:
        return self._matches(self.average)


@dataclass
class _Lot:
    units: float                   # signed: + long, - short
    opened: date
    cash: float | None
    fill: float | None


@dataclass
class _Trip:
    side: str
    opened: date
    option: bool
    pieces: list[Piece] = field(default_factory=list)
    invested: float = 0.0
    peak: float = 0.0
    closed: date | None = None
    income: float = 0.0


@dataclass
class _State:
    lots: dict[tuple[str, str], deque[_Lot]] = field(default_factory=lambda: defaultdict(deque))
    trips: dict[tuple[str, str], list[_Trip]] = field(default_factory=lambda: defaultdict(list))
    average: dict[tuple[str, str], list[float | None]] = field(
        default_factory=lambda: defaultdict(lambda: [0.0, 0.0]))   # [units, cost]


def round_trips(transactions: Iterable[Transaction],
                cash_funds: Iterable[str] = ()) -> tuple[RoundTrip, ...]:
    """Every round trip in the history, oldest opening first (module docstring)."""
    state = _replay(transactions, set(cash_funds))
    trips = []
    for (account, symbol), items in state.trips.items():
        lots = state.lots[(account, symbol)]
        for i, trip in enumerate(items):
            still = sum(abs(l.units) for l in lots) if i == len(items) - 1 and trip.closed is None \
                else 0.0
            trips.append(RoundTrip(account, symbol, trip.side, trip.opened, trip.closed,
                                   tuple(trip.pieces), trip.invested, trip.peak, still,
                                   trip.income, trip.option))
    return tuple(sorted(trips, key=lambda t: (t.opened, t.account, t.symbol)))


def basis_checks(transactions: Sequence[Transaction],
                 snapshots: Sequence[PositionSnapshot]) -> tuple[BasisCheck, ...]:
    """Each long holding with a broker basis, at each snapshot, against FIFO
    and average cost (module docstring)."""
    history = list(transactions)
    known = {t.account for t in history}
    cash_funds = {p.symbol for s in snapshots for p in s.positions if p.kind is Holding.CASH}
    checks = []
    for snapshot in sorted(snapshots, key=lambda s: s.as_of):
        by_account = bool(snapshot.accounts) and set(snapshot.accounts) <= known
        day = snapshot.as_of.date()
        before = _replay((t for t in history if t.trade_date < day), cash_funds)
        through = _replay((t for t in history if t.trade_date <= day), cash_funds)
        broker: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for p in snapshot.positions:
            if p.kind is not Holding.CASH and p.quantity > 0 and p.cost_basis is not None:
                k = (p.account if by_account else "", p.symbol)
                broker[k][0] += p.quantity
                broker[k][1] += p.cost_basis
        for k, (units, basis) in sorted(broker.items()):
            candidates = [_basis(s, k, by_account) for s in (through, before)]
            fifo_units, fifo, average = min(candidates, key=lambda c: abs(c[0] - units))
            checks.append(BasisCheck(snapshot.name, k[0], k[1], units, fifo, average, basis))
    return tuple(checks)


def _basis(state: _State, key: tuple[str, str], by_account: bool):
    """(units, FIFO cost, average cost) of the long lots for ``key``, summed
    across accounts when ``key`` has none."""
    units, fifo, average = 0.0, 0.0, 0.0
    for (account, symbol), lots in state.lots.items():
        if symbol != key[1] or (by_account and account != key[0]):
            continue
        for lot in lots:
            if lot.units > 0:
                units += lot.units
                fifo = None if fifo is None or lot.cash is None else fifo - lot.units * lot.cash
        avg_units, avg_cost = state.average[(account, symbol)]
        average = None if average is None or avg_cost is None else average + avg_cost
    return units, fifo, average


def _ordered(transactions: Iterable[Transaction]) -> list[Transaction]:
    """By day; within a day, opening trades first, otherwise as given."""
    indexed = list(enumerate(transactions))
    indexed.sort(key=lambda it: (it[1].trade_date, it[1].kind not in _OPENERS, it[0]))
    return [t for _, t in indexed]


def _replay(transactions: Iterable[Transaction], cash_funds: set[str]) -> _State:
    state = _State()
    for t in _ordered(transactions):
        key = (t.account, t.symbol)
        if t.kind in _IGNORED or not t.symbol or t.symbol in cash_funds:
            continue
        trips = state.trips[key]
        if t.kind in _INCOME or t.quantity == 0:
            if trips:
                trips[-1].income += t.amount
            continue
        lots = state.lots[key]
        if t.kind is Kind.SPLIT:
            _split(lots, state.average[key], t.quantity)
            continue
        cash = None if t.kind in (Kind.TRANSFER_IN, Kind.TRANSFER_OUT) else t.amount / abs(t.quantity)
        remaining = t.quantity
        while abs(remaining) > EPS and lots and (lots[0].units > 0) != (remaining > 0):
            lot = lots[0]
            m = min(abs(remaining), abs(lot.units))
            side = "long" if lot.units > 0 else "short"
            trips[-1].pieces.append(Piece(
                t.account, t.symbol, side, m, lot.opened, t.trade_date, lot.cash,
                cash, lot.fill,
                0.0 if t.kind is Kind.EXPIRE else t.price, t.option is not None))
            if side == "long":
                _average_close(state.average[key], m)
            lot.units += m if lot.units < 0 else -m
            remaining += m if remaining < 0 else -m
            if abs(lot.units) <= EPS:
                lots.popleft()
            if not lots:
                trips[-1].closed = t.trade_date
        if abs(remaining) > EPS:
            if not lots:
                trips.append(_Trip("long" if remaining > 0 else "short", t.trade_date,
                                   t.option is not None))
            lots.append(_Lot(remaining, t.trade_date, cash, t.price))
            trip = trips[-1]
            if cash is not None:
                trip.invested += abs(remaining) * abs(cash)
            trip.peak = max(trip.peak, sum(abs(l.units) for l in lots))
            if remaining > 0:
                _average_open(state.average[key], remaining, cash)
    return state


def _split(lots: deque[_Lot], average: list[float | None], q: float) -> None:
    held = sum(l.units for l in lots)
    if abs(held) <= EPS:
        return
    ratio = (held + q) / held
    for lot in lots:
        lot.units *= ratio
        if lot.cash is not None:
            lot.cash /= ratio
    average[0] = (average[0] or 0.0) * ratio


def _average_open(average: list[float | None], units: float, cash: float | None) -> None:
    average[0] = (average[0] or 0.0) + units
    average[1] = None if average[1] is None or cash is None else average[1] - units * cash


def _average_close(average: list[float | None], units: float) -> None:
    held, cost = average
    if held is None or held <= EPS:
        return
    if cost is not None:
        average[1] = cost - cost / held * units
    average[0] = held - units
