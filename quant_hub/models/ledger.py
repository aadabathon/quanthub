r"""Replaying a transaction history against positions snapshots (TR2, D92).

A positions snapshot says what was held at one moment; the history says
what changed in between. If the history is complete, the two agree, and
where they don't, the gap is something the history is missing: a
transaction the export didn't include, a row read as the wrong kind, a
corrected row counted twice, or a position held before the history began.
Everything built on the history (lots, round trips, how well each trade was
timed) trusts it, so this check comes first.

The replay
----------
For a holding k (an account and a symbol), the shares held change only by
the history's quantities:

.. math::  N_k(t) = N_k(t_0) + \sum_{i:\ t_0 < d_i \le t,\ k_i = k} q_i

Between two snapshots :math:`S_j` and :math:`S_{j+1}`, on days
:math:`d_j < d_{j+1}`, the **residual** of a holding is

.. math::  e_k = N_k(S_{j+1}) - N_k(S_j) - \sum_{\text{between}} q_i

and the history explains the holding when :math:`|e_k| \le 0.001` shares
(brokers show three decimals).

**Trades on a snapshot's own day.** A snapshot is dated, but the history
has no times, so a trade on :math:`d_j` may have come before or after
:math:`S_j` was taken, and one on :math:`d_{j+1}` before or after
:math:`S_{j+1}`. With :math:`I_k` the quantity strictly between the days,
:math:`s_k` on :math:`d_j` and :math:`t_k` on :math:`d_{j+1}`,

.. math::  e_k(a, b) = N_k(S_{j+1}) - N_k(S_j) - (I_k + a\,s_k + b\,t_k),
           \qquad a, b \in \{0, 1\}

(a = 1: the first day's trades came after the earlier snapshot; b = 1: the
last day's came before the later one). The placement with the smallest
:math:`|e_k|` is kept and named; ties go to a snapshot taken after its
day's trades (a = 0, b = 1), the usual case for an export saved after
trading. Each holding gets its own placement: the history can't say which
of a day's trades came first.

**What moves shares.** Every kind with a quantity except ``other``, which
by definition has no effect (an unmapped row that did move shares shows up
here as a residual). A symbol that a snapshot holds as cash (a money-market
fund) is cash: buying it with cash, or reinvesting its dividend, moves
nothing.

**Accounts.** When every snapshot names its accounts and each is one the
history knows, holdings are compared account by account. A snapshot that
doesn't (an "All Accounts" view, with no account column) can only be
compared by symbol across every account, and then all of them are.

**Opening positions.** Before the first snapshot there is nothing to
compare with but the history itself. Replayed from zero at its first day,
the history should reach the first snapshot exactly; a residual there is a
position held before the history begins, or a gap in it. Those are the
**opening** gaps.

Cash
----
Cash is checked the same way with the amounts: the change in the
snapshots' cash against :math:`\sum A_i` between them, with the same choice
of placements, but it's shown, not passed or failed. Pending activity,
money-market accrual and how a broker splits margin and short cash all
move it by more than a rounding error. Rows in a cash fund that move
shares (buying or reinvesting into it) move cash into cash and are left
out; its dividends are counted.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from quant_hub.data.activity import Kind, Transaction
from quant_hub.data.positions import Holding, PositionSnapshot

SHARE_TOLERANCE = 1e-3        # shares: brokers show three decimals

# (a, b) in the order ties are broken: a snapshot taken after its day's trades first.
_PLACEMENTS = ((0, 1), (1, 1), (0, 0), (1, 0))


@dataclass(frozen=True)
class HoldingGap:
    """One holding between two snapshots (or from the history's start to
    the first snapshot): what each held, and what the history traded."""

    account: str                   # "" when compared by symbol across accounts
    symbol: str
    before: float
    after: float
    traded: float
    placement: str = ""            # how same-day trades were placed, when there were any

    @property
    def residual(self) -> float:
        return self.after - self.before - self.traded

    @property
    def reconciled(self) -> bool:
        return abs(self.residual) <= SHARE_TOLERANCE


@dataclass(frozen=True)
class Interval:
    """Two consecutive snapshots and every holding either held, or the
    history traded, between them."""

    start: datetime
    end: datetime
    start_name: str
    end_name: str
    holdings: tuple[HoldingGap, ...]
    cash_before: float
    cash_after: float
    cash_moved: float

    @property
    def gaps(self) -> tuple[HoldingGap, ...]:
        return tuple(h for h in self.holdings if not h.reconciled)

    @property
    def reconciled(self) -> bool:
        return not self.gaps

    @property
    def cash_residual(self) -> float:
        return self.cash_after - self.cash_before - self.cash_moved


@dataclass(frozen=True)
class Reconciliation:
    """The whole check (module docstring)."""

    by_account: bool
    first_day: date | None         # the history's first and last trade dates
    last_day: date | None
    opening: tuple[HoldingGap, ...]   # unexplained at the first snapshot
    intervals: tuple[Interval, ...]
    snapshots: int = 0                # how many were replayed
    first_snapshot: date | None = None

    @property
    def reconciled(self) -> bool:
        return not self.opening and all(i.reconciled for i in self.intervals)


def reconcile(transactions: Iterable[Transaction],
              snapshots: Sequence[PositionSnapshot]) -> Reconciliation:
    """Replay ``transactions`` against ``snapshots`` (module docstring).
    The snapshots must fall on different days; their order doesn't matter."""
    history = list(transactions)
    ordered = sorted(snapshots, key=lambda s: s.as_of)
    days = [s.as_of.date() for s in ordered]
    if len(set(days)) != len(days):
        raise ValueError("two snapshots fall on the same day: keep one per day")
    known = {t.account for t in history}
    by_account = bool(ordered) and all(s.accounts and set(s.accounts) <= known
                                       for s in ordered)
    cash_funds = {p.symbol for s in ordered for p in s.positions if p.kind is Holding.CASH}

    def key(account: str, symbol: str) -> tuple[str, str]:
        return (account if by_account else "", symbol)

    shares: dict[date, dict[tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
    cash: dict[date, float] = defaultdict(float)
    for t in history:
        if t.kind is Kind.OTHER:
            continue
        moves_shares = t.quantity != 0 and t.symbol not in cash_funds
        if moves_shares:
            shares[t.trade_date][key(t.account, t.symbol)] += t.quantity
        if not (t.quantity != 0 and t.symbol in cash_funds):
            cash[t.trade_date] += t.amount

    held = [_holdings(s, key) for s in ordered]
    cash_held = [_cash(s) for s in ordered]

    opening: tuple[HoldingGap, ...] = ()
    if ordered:
        first = days[0]
        before_first = _sum_between(shares, None, first)
        on_first = shares.get(first, {})
        gaps = []
        for k in sorted(set(held[0]) | set(before_first) | set(on_first)):
            inner, last = before_first.get(k, 0.0), on_first.get(k, 0.0)
            options = [(b, inner + b * last) for b in (1, 0)]
            b, traded = min(options, key=lambda o: abs(held[0].get(k, 0.0) - o[1]))
            gap = HoldingGap(k[0], k[1], 0.0, held[0].get(k, 0.0), traded,
                             _placement(None, first, 0, b, 0.0, last))
            if not gap.reconciled:
                gaps.append(gap)
        opening = tuple(gaps)

    intervals = []
    for j in range(len(ordered) - 1):
        d0, d1 = days[j], days[j + 1]
        inner = _sum_between(shares, d0, d1)
        s, t = shares.get(d0, {}), shares.get(d1, {})
        holdings = []
        for k in sorted(set(held[j]) | set(held[j + 1]) | set(inner) | set(s) | set(t)):
            before, after = held[j].get(k, 0.0), held[j + 1].get(k, 0.0)
            a, b = min(_PLACEMENTS, key=lambda ab: abs(
                after - before - (inner.get(k, 0.0) + ab[0] * s.get(k, 0.0)
                                  + ab[1] * t.get(k, 0.0))))
            traded = inner.get(k, 0.0) + a * s.get(k, 0.0) + b * t.get(k, 0.0)
            holdings.append(HoldingGap(k[0], k[1], before, after, traded,
                                       _placement(d0, d1, a, b, s.get(k, 0.0), t.get(k, 0.0))))
        moved_inner = sum(v for d, v in cash.items() if d0 < d < d1)
        c0, c1 = cash.get(d0, 0.0), cash.get(d1, 0.0)
        moved = min((moved_inner + a * c0 + b * c1 for a, b in _PLACEMENTS),
                    key=lambda m: abs(cash_held[j + 1] - cash_held[j] - m))
        intervals.append(Interval(ordered[j].as_of, ordered[j + 1].as_of, ordered[j].name,
                                  ordered[j + 1].name, tuple(holdings), cash_held[j],
                                  cash_held[j + 1], moved))

    trade_days = [t.trade_date for t in history]
    return Reconciliation(by_account, min(trade_days, default=None),
                          max(trade_days, default=None), opening, tuple(intervals),
                          len(ordered), days[0] if days else None)


def _holdings(snapshot: PositionSnapshot, key) -> dict[tuple[str, str], float]:
    held: dict[tuple[str, str], float] = defaultdict(float)
    for p in snapshot.positions:
        if p.kind is not Holding.CASH:
            held[key(p.account, p.symbol)] += p.quantity
    return dict(held)


def _cash(snapshot: PositionSnapshot) -> float:
    return sum(p.market_value or 0.0 for p in snapshot.positions if p.kind is Holding.CASH)


def _sum_between(shares, start: date | None, end: date) -> dict[tuple[str, str], float]:
    """Each holding's quantity on days strictly between ``start`` (None: the
    beginning) and ``end``."""
    total: dict[tuple[str, str], float] = defaultdict(float)
    for day, moves in shares.items():
        if (start is None or day > start) and day < end:
            for k, q in moves.items():
                total[k] += q
    return dict(total)


def _placement(d0: date | None, d1: date, a: int, b: int, s: float, t: float) -> str:
    parts = []
    if d0 is not None and s:
        parts.append(f"{d0}'s trades {'after' if a else 'before'} the earlier snapshot")
    if t:
        parts.append(f"{d1}'s trades {'before' if b else 'after'} the later snapshot")
    return "; ".join(parts)
