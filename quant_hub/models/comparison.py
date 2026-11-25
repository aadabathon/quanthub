r"""The desk's voices side by side, in dollars (D3).

Every voice makes the same claims about the same book (``models/baseline.py``,
``models/voices.py``). This module puts those claims in dollars and lines
them up, so it's plain where the voices agree and where they don't. It adds
no model of its own: every number here is a claim already in the snapshot,
multiplied by the book's value.

Dollars
-------
At ``as_of`` the book is worth :math:`V = \sum_i n_i P_i + C`: its priced
holdings and its cash. A claim's return over h sessions holds the units and
the cash unchanged, with cash earning nothing:

.. math::  R_h = \frac{1}{V} \sum_i n_i\,(P_{i,h} - P_i)

So the book's dollar change is :math:`D_h = V R_h`. When V > 0 that's
increasing in :math:`R_h`, so every quantile carries over exactly:
:math:`q_\tau(D_h) = V\,q_\tau(R_h)`. When V ≤ 0 (debt beyond the
holdings) a return on V means nothing, and the comparison refuses.

A chance of a fall to k is a chance of losing :math:`V|k|` dollars. A
realized volatility σ (annualized, from daily log returns) is a typical
daily move of about :math:`V\sigma/\sqrt{252}` dollars. That last one is
first order (:math:`e^{r} - 1 \approx r` for one day's r) and is labelled
"about".

Agreement and disagreement
--------------------------
For each claim on the book that the voices make (same target, horizon and
level, or same event), the comparison keeps every voice's value: the
lowest, the highest, and how far apart they are, in dollars (a probability
for an event, an annual volatility for ``book.vol``). **Nothing is
averaged.** A consensus number would hide the disagreement the desk is
there to show.

Each voice is also set against the **baseline**, the no-edge voice. Every
other voice differs from it in one assumption, so the difference is what
that assumption says today, in dollars. Whether it's worth listening to is
D4's score.

Proposals in dollars
--------------------
A proposal gives target weights :math:`w'_i` of V; cash is what's left. A
held symbol the proposal leaves out goes to zero, and a symbol the book
doesn't hold is a new buy. The trade in holding i is :math:`(w'_i - w_i)V`
dollars, or :math:`(w'_i - w_i)V/P_i` units, and the cash it raises is
minus their sum.

What a proposal does, under each voice's law
--------------------------------------------
Suppose a proposal scales every holding by the same :math:`m \ge 0`
(:math:`w'_i = m w_i`), as the volatility-managed voices do. Then the
scaled book holds :math:`m n_i` units, and the cash it raises earns
nothing. So its dollar change is **exactly** :math:`m D_h`, and under any
voice's law its quantiles are :math:`m V q_\tau`. The change from holding
is :math:`(m - 1) V q_\tau`:

* the bad tail shrinks by :math:`(1 - m) V |q_{0.05}|`;
* the median gives up :math:`(1 - m) V q_{0.5}`.

That's the trade-off in dollars. Under a voice with no drift (the
baseline, GARCH, the bootstrap), scaling down costs nothing at the median
and only saves in the tail. Under the regimes voice, which claims a drift,
it gives up that drift. Reading one voice's proposal under another voice's
law is where disagreement turns into dollars.

Two things are **not** carried over:

* **A proposal that changes the mix** (not one m for every holding). The
  book's claims say nothing about another mix; that would take the
  holdings' joint law. Its trades are shown, its effect isn't. A voice that
  proposes one should claim its own effect.
* **The chance of a fall.** :math:`P(mR \le k) = P(R \le k/m)` is a claim
  at another threshold, which no voice made.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from quant_hub.data.desk import LEVELS, Book, Proposal, Snapshot

BASELINE = "baseline"
TRADING_DAYS = 252
_SCALE_TOLERANCE = 1e-9            # relative: w'_i = m w_i for every holding


@dataclass(frozen=True)
class Across:
    """One claim on the book, every voice that made it (module docstring).

    ``kind`` is ``"dollars"`` (a ``book.return`` quantile, as V q),
    ``"vol"`` (a ``book.vol`` quantile, annualized) or ``"probability"`` (an
    event). ``values`` maps each voice to its value, in the order the voices
    spoke."""

    target: str
    horizon: int
    kind: str
    level: float | None
    event: str
    values: Mapping[str, float]

    @property
    def low(self) -> tuple[str, float]:
        voice = min(self.values, key=self.values.__getitem__)
        return voice, self.values[voice]

    @property
    def high(self) -> tuple[str, float]:
        voice = max(self.values, key=self.values.__getitem__)
        return voice, self.values[voice]

    @property
    def apart(self) -> float:
        return self.high[1] - self.low[1]

    def against(self, reference: str = BASELINE) -> dict[str, float]:
        """Each other voice's value minus ``reference``'s; empty when the
        reference didn't make this claim."""
        if reference not in self.values:
            return {}
        base = self.values[reference]
        return {v: x - base for v, x in self.values.items() if v != reference}


@dataclass(frozen=True)
class Trade:
    symbol: str
    dollars: float                 # + buys, − sells
    units: float | None            # None for a symbol the book doesn't price


@dataclass(frozen=True)
class Proposed:
    """One voice's proposal in dollars (module docstring). ``scale`` is m
    when the proposal scales every holding alike (1 for holding), else
    None."""

    voice: str
    hold: bool
    note: str
    trades: tuple[Trade, ...]
    to_cash: float
    scale: float | None


@dataclass(frozen=True)
class Effect:
    """A uniform-scale proposal read under one voice's law: the book's
    dollar quantiles at ``LEVELS`` over ``horizon``, held and proposed."""

    proposal: str                  # the voice that proposed it
    law: str                       # the voice whose claims it's read under
    horizon: int
    held: tuple[float, ...]
    proposed: tuple[float, ...]

    @property
    def change(self) -> tuple[float, ...]:
        return tuple(b - a for a, b in zip(self.held, self.proposed))


@dataclass(frozen=True)
class Comparison:
    """A snapshot's voices side by side (module docstring)."""

    value: float                   # V, the book's value at as_of
    voices: tuple[str, ...]        # in the order they spoke
    across: tuple[Across, ...]
    proposals: tuple[Proposed, ...]
    effects: tuple[Effect, ...]

    def horizons(self) -> tuple[int, ...]:
        """The horizons with ``book.return`` quantiles, shortest first."""
        return tuple(sorted({a.horizon for a in self.across
                             if a.target == "book.return" and a.kind == "dollars"}))

    def band(self, voice: str, horizon: int, target: str = "book.return") -> tuple[float, ...]:
        """``voice``'s quantiles of ``target`` over ``horizon`` at ``LEVELS``
        (dollars for ``book.return``), or () when it made no such claim."""
        found = {a.level: a.values[voice] for a in self.across
                 if a.target == target and a.horizon == horizon and a.level is not None
                 and voice in a.values}
        return tuple(found[t] for t in LEVELS) if len(found) == len(LEVELS) else ()

    def daily_move(self, annual_vol: float) -> float:
        """About how many dollars a day an annual volatility moves the book."""
        return self.value * annual_vol / math.sqrt(TRADING_DAYS)


def compare(snapshot: Snapshot) -> Comparison:
    """``snapshot``'s claims on the book, in dollars and side by side, its
    proposals in dollars, and what each uniform-scale proposal does under
    every voice's law (module docstring)."""
    book = snapshot.book
    value = book.value
    if not value > 0:
        raise ValueError(f"the book is worth {value:,.2f}: a return on it means nothing, "
                         "so the voices can't be put in dollars")
    voices = tuple(v.voice for v in snapshot.voices)
    rows: dict[tuple, dict[str, float]] = {}
    for output in snapshot.voices:
        for claim in output.claims:
            if not claim.target.startswith("book."):
                continue
            if claim.kind == "quantiles":
                kind = "dollars" if claim.target == "book.return" else "vol"
                for level, q in zip(LEVELS, claim.quantiles):
                    x = value * q if kind == "dollars" else q
                    rows.setdefault((claim.target, claim.horizon, kind, level, ""),
                                    {})[output.voice] = x
            else:
                rows.setdefault((claim.target, claim.horizon, "probability", None, claim.event),
                                {})[output.voice] = claim.probability
    order = {"dollars": 0, "probability": 1, "vol": 2}
    across = tuple(Across(t, h, k, lv, ev, values) for (t, h, k, lv, ev), values in
                   sorted(rows.items(), key=lambda item: (order[item[0][2]], item[0][1],
                                                          item[0][3] or 0.0, item[0][4])))
    proposals = tuple(_proposed(o.voice, o.proposal, book) for o in snapshot.voices)
    comparison = Comparison(value, voices, across, proposals, ())
    effects = []
    for p in proposals:
        if p.hold or p.scale is None:
            continue
        for law in voices:
            for h in comparison.horizons():
                held = comparison.band(law, h)
                if held:
                    effects.append(Effect(p.voice, law, h, held,
                                          tuple(p.scale * x for x in held)))
    return Comparison(value, voices, across, proposals, tuple(effects))


def _proposed(voice: str, proposal: Proposal | None, book: Book) -> Proposed:
    if proposal is None:
        return Proposed(voice, True, "no proposal", (), 0.0, 1.0)
    if proposal.hold:
        return Proposed(voice, True, proposal.note or "hold", (), 0.0, 1.0)
    value, now = book.value, book.weights()
    prices = {h.symbol: h.price for h in book.holdings}
    trades = []
    for symbol in [*now, *(s for s in proposal.weights if s not in now)]:
        dollars = (proposal.weights.get(symbol, 0.0) - now.get(symbol, 0.0)) * value
        if abs(dollars) > 1e-9:
            price = prices.get(symbol)
            trades.append(Trade(symbol, dollars, dollars / price if price else None))
    return Proposed(voice, False, proposal.note, tuple(trades),
                    -sum(t.dollars for t in trades), scale_of(proposal, book))


def scale_of(proposal: Proposal, book: Book) -> float | None:
    """m when ``proposal`` holds every holding at m times its weight and
    adds nothing; None when it changes the mix."""
    now = book.weights()
    if any(abs(w) > 0 for s, w in proposal.weights.items() if s not in now):
        return None
    held = [(s, w) for s, w in now.items() if w != 0]
    if not held:
        return None
    symbol, weight = max(held, key=lambda sw: abs(sw[1]))
    m = proposal.weights.get(symbol, 0.0) / weight
    if m < 0:
        return None
    for s, w in held:
        if abs(proposal.weights.get(s, 0.0) - m * w) > _SCALE_TOLERANCE * max(1.0, abs(w)):
            return None
    return m


def holding_bands(snapshot: Snapshot, voice: str = BASELINE,
                  horizon: int = 21) -> dict[str, tuple[float, ...]]:
    """Each holding's dollar quantiles over ``horizon`` from ``voice``'s
    ``SYM.return`` claims: its value times each quantile, as for the book.
    A short's value is negative, which turns the order around: its
    τ-quantile is its value times the return's (1 − τ)-quantile, and since
    ``LEVELS`` is symmetric about the median that's the quantiles read
    backwards."""
    claims = next((o.claims for o in snapshot.voices if o.voice == voice), ())
    values = {h.symbol: h.value for h in snapshot.book.holdings}
    bands = {}
    for claim in claims:
        symbol, _, what = claim.target.rpartition(".")
        if (symbol in values and what == "return" and claim.horizon == horizon
                and claim.kind == "quantiles"):
            qs = claim.quantiles if values[symbol] >= 0 else claim.quantiles[::-1]
            bands[symbol] = tuple(values[symbol] * q for q in qs)
    return bands
