r"""A trade you're weighing, on the desk's book at the snapshot's close (A3).

You're weighing some trades. What does each voice say about the book after
them? ``apply`` makes that book. ``DeskService.whatif`` (``services/desk.py``)
then has every voice speak on both books, from the same data, and the two
are set side by side in dollars (``services/desk_report.py``). Nothing is
stored: a what-if is neither a snapshot nor a decision.

The book after
--------------
At the snapshot's ``as_of`` t, the book holds units :math:`n_i` at closes
:math:`P_i`, plus cash C, so it's worth :math:`V = \sum_i n_i P_i + C`.

A trade of :math:`u_k` units of k (+ buys, − sells) goes through at k's
close on t, :math:`P_k`. For a held name that's the snapshot's own price;
for a name not held, it's the close on t from the snapshot's source.
``SYM ±$D`` is read as :math:`u_k = D / P_k`. Then

.. math::  n_i' = n_i + u_i, \qquad C' = C - \sum_k u_k P_k,

so :math:`V' = \sum_i n_i' P_i + C' = V`. A trade at the close moves value
between holdings and cash, and changes nothing else at t. That's why the
two books' claims compare dollar for dollar. Two more rules:
- A name traded to zero leaves the book.
- No commission is charged (Fidelity charges none on stocks).

Flagged, not refused
--------------------
* **A short:** a sale past the units held leaves :math:`n_i' < 0`.
* **Margin:** :math:`C' < 0`. The claims hold cash at zero interest, so
  the loan's cost isn't in them.

Refused
-------
* **No price to trade at:** the snapshot couldn't price the name (an
  option, or a symbol without bars), so it isn't in the book.
* **No trades,** or a trade of zero.
* **A book after worth V ≤ 0,** which the comparison refuses for any book.

What it doesn't know
--------------------
* **Today's fill:** the price is the close on t, and the claims' bands are
  far wider than a day's move.
* **Taxes** on a sale's gain.
* **Interest** on cash or a loan.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from quant_hub.data.desk import Book, Holding

_ZERO = 1e-9                     # units: a name traded to this leaves the book


@dataclass(frozen=True)
class Move:
    """One trade weighed: ``amount`` units of ``symbol``, or dollars of it
    when ``in_dollars`` (+ buys, − sells)."""

    symbol: str
    amount: float
    in_dollars: bool = False


@dataclass(frozen=True)
class Traded:
    """The book after the moves (module docstring), each trade at its
    price, and what's flagged."""

    book: Book
    trades: tuple[tuple[str, float, float], ...]     # (symbol, units, price), merged
    shorts: tuple[str, ...]                          # names held short after
    margin: bool                                     # cash below zero after

    @property
    def to_cash(self) -> float:
        """What the trades put into cash (− when they spend it)."""
        return -sum(units * price for _, units, price in self.trades)


def parse_move(text: str) -> Move:
    """``MSFT +5``, ``MSFT -2.5``, ``MSFT -$1,000`` or ``MSFT:+5`` as a Move.
    A ValueError says what's wrong."""
    symbol, _, amount = text.strip().replace(":", " ").partition(" ")
    raw = amount.replace(" ", "").replace(",", "").replace("−", "-")
    in_dollars = "$" in raw
    raw = raw.replace("$", "")
    if not symbol or not raw:
        raise ValueError(f"{text.strip()!r}: a trade is SYMBOL and units or dollars, "
                         "e.g. MSFT +5 or MSFT -$500")
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{text.strip()!r}: {amount.strip()!r} isn't units or dollars") \
            from None
    if not math.isfinite(value) or value == 0:
        raise ValueError(f"{text.strip()!r}: a trade moves something")
    return Move(symbol.upper(), value, in_dollars)


def apply(book: Book, moves: Sequence[Move], prices: Mapping[str, float]) -> Traded:
    """The book after ``moves`` at ``prices``, the closes on the book's
    ``as_of`` (module docstring). ``prices`` must hold every traded name a
    book doesn't price itself."""
    if not moves:
        raise ValueError("no trades to weigh")
    left_out = {u.split(" ")[0] for u in book.unpriced}
    held = {h.symbol: h for h in book.holdings}
    units: dict[str, float] = {}
    price_of: dict[str, float] = {}
    for move in moves:
        if move.symbol in left_out and move.symbol not in held:
            raise ValueError(f"{move.symbol} isn't priced in this snapshot, so there's no "
                             "price to trade it at")
        price = held[move.symbol].price if move.symbol in held else prices.get(move.symbol)
        if price is None or not price > 0:
            raise ValueError(f"no close for {move.symbol} on {book.as_of} to trade it at")
        price_of[move.symbol] = price
        delta = move.amount / price if move.in_dollars else move.amount
        units[move.symbol] = units.get(move.symbol, 0.0) + delta
    holdings = []
    for h in book.holdings:
        after = h.units + units.get(h.symbol, 0.0)
        if abs(after) > _ZERO:
            holdings.append(Holding(h.symbol, after, h.price))
    for symbol, delta in units.items():
        if symbol not in held and abs(delta) > _ZERO:
            holdings.append(Holding(symbol, delta, price_of[symbol]))
    trades = tuple((s, u, price_of[s]) for s, u in units.items() if abs(u) > _ZERO)
    cash = book.cash - sum(u * p for _, u, p in trades)
    after = Book(book.as_of, tuple(holdings), cash, book.unpriced, book.units_as_of,
                 book.market, book.market_price)
    shorts = tuple(h.symbol for h in holdings if h.units < 0)
    return Traded(after, trades, shorts, cash < 0)
