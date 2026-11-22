"""Today's book, and how to analyse it: what What-if and the Hedge Lab read (P2a, D89).

**Why a seam.** What-if compares weight vectors against today's book, and
the Hedge Lab's "today's mix" replays it. Both need the same three things:
- **the positions:** the newest snapshot
- **the analysis settings:** the history source, benchmark, lookback and
  cash rate
- **the grouping:** which underlyings form a sleeve, and which names are the
  book's core (What-if's netting builder nets into it)

Where those come from isn't either page's business, so both ask a
``BookSource`` for a ``Book``.

**Quant Hub's own source, ``PortfolioBook``,** is the book as the Portfolio
page analyses it:
- **The positions:** the newest snapshot stored on the Portfolio page,
  imported or read From IBKR, all accounts together. With none stored, it's
  the example portfolio Portfolio shows before any import, and the book's
  label says so.
- **The settings:** the Portfolio page's own "Prices from", benchmark,
  lookback and rate, read from the record the page saves
  (``PORTFOLIO_STATE_KEY``), so What-if analyses the book exactly as
  Portfolio does. A value that Portfolio would refuse is named, with where
  to fix it.
- **No sleeves and no core.** Each underlying is its own sleeve, and the
  netting builder nets into the benchmark.
- **No β to suggest:** ``latest_beta`` is None, so the Hedge Lab starts at
  1.00 until you type one.

**An extension can supply its own source** (``Extension.book``, D87's hook):
its own settings, sleeves and core. It can also supply a ``Grader``
(``Extension.grader``), which grades every What-if vector against its own
rules. Quant Hub has no rules of its own, so without a grader What-if hides
its Fails and Warns rows.

**What a grader returns** (``Grades``):
- the fails and warns it counted
- the effective-holdings figure it measures (looked through a core, say);
  None leaves it blank, so a grader that can't measure it says why in
  ``notes``
- ``notes``: why a measure is blank, each said once per comparison
- ``detail``: the grader's own record, which Quant Hub never reads

Reading a book analyses it, so ``book`` reads bars: call it on the worker
thread, as the pages do. A missing history is named, never fetched behind
your back (V0): the analysis reads through the chosen source.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from quant_hub.data.positions import PositionSnapshot
from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import PortfolioService, PortfolioView, example_snapshot
from quant_hub.storage.state_store import ViewStateStore

#: The record the Portfolio page saves its fields in, and PortfolioBook reads.
PORTFOLIO_STATE_KEY = "portfolio"
#: The Portfolio page's defaults, as its fields hold them (text).
DEFAULT_SOURCE = "ibkr"
DEFAULT_BENCHMARK = "SPY"
DEFAULT_LOOKBACK = "730"           # calendar days
DEFAULT_RATE = "4.0"               # percent a year
MIN_LOOKBACK = 120                 # the page refuses a shorter one


class BookError(ValueError):
    """Today's book can't be read; the message says what to fix, and where."""


@dataclass(frozen=True, eq=False)
class Book:
    """Today's book, analysed, and the settings every comparison reuses."""

    view: PortfolioView                 # today's positions through the analysis
    snapshot: PositionSnapshot
    source: str                         # the history source
    benchmark: str
    lookback: int                       # calendar days
    rate: float                         # the cash rate, a fraction a year
    sleeves: Mapping[str, str] = field(default_factory=dict)   # underlying -> sleeve
    core: frozenset[str] = frozenset()  # the book's core names
    label: str = ""                     # where the book came from, for the pages


@runtime_checkable
class BookSource(Protocol):
    def book(self, now: datetime | None = None) -> Book:
        """Today's book (reads bars: call it on the worker thread)."""

    def latest_beta(self) -> float | None:
        """A β for the Hedge Lab to start from, or None."""


@dataclass(frozen=True)
class Grades:
    """One vector graded by an extension's rules (module docstring)."""

    fails: int
    warns: int
    effective: float | None             # N_eff as the grader measures it; None: blank
    notes: tuple[str, ...] = ()
    detail: object = None               # the grader's own record; Quant Hub never reads it


@runtime_checkable
class Grader(Protocol):
    def grade(self, view: PortfolioView, snapshot: PositionSnapshot, book: Book) -> Grades:
        """Grade one vector, analysed as ``view``, against today's ``book``."""


@dataclass(frozen=True)
class PortfolioSettings:
    """The Portfolio page's analysis settings, validated as the page does."""

    source: str
    benchmark: str
    lookback: int
    rate: float


def portfolio_settings(state: ViewStateStore, sources: Sequence[str]) -> PortfolioSettings:
    """The settings the Portfolio page last saved, else its defaults.

    The page saves its fields as typed, so they're checked here as the page
    checks them before it analyses. Raises ``BookError`` naming the field.
    """
    saved = state.load(PORTFOLIO_STATE_KEY)
    if not sources:
        raise BookError("no price source is registered")
    source = str(saved.get("source", DEFAULT_SOURCE))
    if source not in sources:                       # the page falls back the same way
        source = DEFAULT_SOURCE if DEFAULT_SOURCE in sources else sources[0]
    benchmark = str(saved.get("benchmark", DEFAULT_BENCHMARK)).strip().upper()
    if not benchmark:
        raise BookError("Portfolio's Benchmark is empty: set one on the Portfolio page")
    try:
        lookback = int(str(saved.get("lookback", DEFAULT_LOOKBACK)).strip())
        if lookback < MIN_LOOKBACK:
            raise ValueError
    except ValueError:
        raise BookError(f"Portfolio's Lookback must be a whole number of days, at least "
                        f"{MIN_LOOKBACK}: fix it on the Portfolio page") from None
    try:
        rate = float(str(saved.get("rate", DEFAULT_RATE)).strip()) / 100.0
    except ValueError:
        raise BookError("Portfolio's Rate % isn't a number: fix it on the Portfolio page") from None
    return PortfolioSettings(source, benchmark, lookback, rate)


class PortfolioBook:
    """Quant Hub's ``BookSource``: the book as the Portfolio page analyses it."""

    def __init__(self, portfolio: PortfolioService, market: MarketService,
                 state: ViewStateStore, now: Callable[[], datetime] = datetime.now) -> None:
        self._portfolio = portfolio
        self._market = market
        self._state = state
        self._now = now

    def book(self, now: datetime | None = None) -> Book:
        settings = portfolio_settings(self._state, self._market.source_names)
        summaries = self._portfolio.snapshots()
        if summaries:
            snapshot = self._portfolio.load(summaries[0].id)
            label = f"{snapshot.name}, the newest snapshot on Portfolio"
        else:
            try:
                snapshot = example_snapshot(self._market, settings.source)
            except Exception as exc:
                raise BookError(f"no positions are stored, and the example portfolio needs "
                                f"{settings.source} history ({exc}). Import positions on the "
                                "Portfolio page") from exc
            label = "the example portfolio: import yours on the Portfolio page"
        view = self._portfolio.analyse(snapshot, None, settings.source, settings.benchmark,
                                       settings.lookback, settings.rate,
                                       now=now or self._now())
        return Book(view, snapshot, settings.source, settings.benchmark, settings.lookback,
                    settings.rate, label=label)

    def latest_beta(self) -> float | None:
        return None
