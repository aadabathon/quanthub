"""The What-if page's job: weight vectors through Portfolio's analysis (ALLOCATION A3, D72, D89).

A vector (``models/whatif.py``) is a set of weights, the rest cash. It's
never stored as a positions snapshot, so a what-if can't be mistaken for a
trade. Named sets are kept in the view-state store.

**The context** (``context``): today's book from the ``BookSource``
(``services/book.py``). That fixes the analysis every vector gets: the
book's history source, benchmark, lookback, cash rate and sleeves. The
context also carries:
- today's vector: each holding's market value over equity (stocks and
  funds; options aren't vector weights)
- each name's β, for the netting builder
- the book's core, if it names one
- the lookback's daily returns, for the references

**The builders** (``models/whatif.py``): today; today with the picks
scaled to a total; an index and cash; and today's picks netted into the
core at a target β (the benchmark when the book names no core). Plus the
references on today's names and invested share: minimum variance, equal
risk, equal weight, and maximum Sharpe, in-sample and labelled so.

**The comparison** (``compare``), one TaskRunner job. Each vector gets:
- ``PortfolioService.analyse``, Portfolio's own analysis
- the crash stress (A2, ``models/stress.py``): the vector's β, with its
  Newey–West error where the regression has one, replayed through Ken
  French's daily market since 1926. Without the stored series it's blank,
  with how to fetch it.
- the ``Grader``'s grades, when one is given (an extension's rules)

The table reads off each: β ± SE, vol, VaR, ES, the lookback's drawdown,
the crash stress, N_eff, the top risk share, growth, and with a grader the
fails and warns.

**Missing bars are named, never fetched behind your back** (V0's offline
rule): the analysis reads through the book's history source, and a vector
whose names have no history says so.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from quant_hub.models import whatif as W
from quant_hub.models.stress import CrashStress, crash_stress
from quant_hub.services.book import Book, BookSource, Grader, Grades
from quant_hub.services.external_data import ExternalDataService
from quant_hub.services.portfolio import PORTFOLIO_ROW, PortfolioService, PortfolioView
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

STATE_KEY = "whatif.sets"
MAX_SHARPE = "Max Sharpe (in-sample)"
REFERENCES = ("Minimum variance", "Equal risk", "Equal weight", MAX_SHARPE)
CRASH_MARKET = ("french", "ff3.mkt_rf")      # the market's daily excess return, from 1926
CRASH_BILLS = ("french", "ff3.rf")           # the T-bill's daily return


@dataclass(frozen=True, eq=False)
class Context:
    """What every vector is analysed against (module docstring)."""

    book: Book
    today: dict[str, float]
    betas: dict[str, float]
    as_of: datetime

    @property
    def source(self) -> str:
        return self.book.source

    @property
    def benchmark(self) -> str:
        return self.book.benchmark

    @property
    def lookback(self) -> int:
        return self.book.lookback

    @property
    def rate(self) -> float:
        return self.book.rate

    @property
    def core(self) -> frozenset[str]:
        return self.book.core

    @property
    def returns(self) -> pd.DataFrame | None:
        return self.book.view.returns


@dataclass(frozen=True, eq=False)
class Row:
    """One vector, analysed, stressed and (with a grader) graded."""

    name: str
    weights: dict[str, float]
    view: PortfolioView | None = None
    grades: Grades | None = None
    crash: CrashStress | None = None
    crash_note: str = ""
    error: str = ""

    @property
    def beta(self) -> tuple[float, float | None] | None:
        if self.view is None:
            return None
        if self.view.regressions is not None:
            return self.view.regressions.beta(PORTFOLIO_ROW)
        beta = self.view.report.beta
        return (beta, None) if beta is not None else None

    @property
    def top_risk(self) -> tuple[str, float] | None:
        if self.view is None or not self.view.rows:
            return None
        top = max(self.view.rows, key=lambda r: r.risk_share)
        return top.symbol, top.risk_share

    @property
    def effective(self) -> float | None:
        """N_eff: the grader's measure when there's a grader, else the report's."""
        if self.grades is not None:
            return self.grades.effective
        return self.view.report.n_effective if self.view is not None else None

    @property
    def fails(self) -> int:
        return self.grades.fails if self.grades else 0

    @property
    def warns(self) -> int:
        return self.grades.warns if self.grades else 0


@dataclass(frozen=True)
class Comparison:
    rows: tuple[Row, ...]
    context_note: str = ""
    notes: tuple[str, ...] = field(default=())


class WhatIfService:
    def __init__(self, portfolio: PortfolioService, book: BookSource, state: ViewStateStore,
                 external: ExternalDataService | None = None, grader: Grader | None = None,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self._portfolio = portfolio
        self._book = book
        self._state = state
        self._external = external
        self._grader = grader
        self._now = now

    @property
    def graded(self) -> bool:
        """Whether vectors get fails and warns (an extension's grader)."""
        return self._grader is not None

    # ------------------------------------------------------------ context
    def context(self) -> Context:
        """Today's book and the analysis settings (module docstring).
        Raises the book source's error, which names what's missing."""
        as_of = self._now()
        book = self._book.book(now=as_of)
        view = book.view
        equity = view.report.equity
        today = W.validate({row.symbol: row.value / equity for row in view.rows if row.value > 0})
        betas = {row.symbol: row.beta for row in view.rows if row.beta is not None}
        return Context(book, today, betas, as_of)

    # ----------------------------------------------------------- builders
    def scaled(self, ctx: Context, total: float) -> dict[str, float]:
        return W.scale_picks(ctx.today, ctx.core, total)

    def netted(self, ctx: Context, target: float, core: str | None = None) -> dict[str, float]:
        """Today's picks, and the core at the weight that brings the book to
        ``target``. ``core`` defaults to the book's (when it names one),
        else the benchmark."""
        core = (core or (next(iter(sorted(ctx.core))) if len(ctx.core) == 1 else ctx.benchmark))
        core = core.upper()
        picks = {s: w for s, w in ctx.today.items() if s not in ctx.core and s != core}
        core_beta = ctx.betas.get(core, 1.0 if core == ctx.benchmark else None)
        if core_beta is None:
            raise ValueError(f"no β for the core {core}: it isn't held today")
        return W.net_into_core(picks, ctx.betas, core, core_beta, target)

    def references(self, ctx: Context) -> dict[str, dict[str, float]]:
        """The four references on today's names and invested share."""
        if ctx.returns is None:
            raise ValueError("no returns over the lookback for the references")
        names = sorted(ctx.today)
        share = sum(ctx.today.values())
        return {"Minimum variance": W.minimum_variance(ctx.returns, names, share),
                "Equal risk": W.equal_risk(ctx.returns, names, share),
                "Equal weight": W.equal_weight(names, share),
                MAX_SHARPE: W.max_sharpe(ctx.returns, names, share, ctx.rate)}

    # --------------------------------------------------------- comparison
    def compare(self, ctx: Context, vectors: Mapping[str, Mapping[str, float]]) -> Comparison:
        """Every vector through the analysis, the crash replay and, with a
        grader, the grades (module docstring)."""
        book = ctx.book
        rows = []
        for name, weights in vectors.items():
            try:
                weights = W.validate(weights)
                snapshot = W.to_snapshot(weights, name, ctx.as_of)
                view = self._portfolio.analyse(snapshot, None, book.source, book.benchmark,
                                               book.lookback, book.rate, now=ctx.as_of,
                                               sleeves=dict(book.sleeves))
                crash, crash_note = self._crash(view)
                grades = (self._grader.grade(view, snapshot, book)
                          if self._grader is not None else None)
                rows.append(Row(name, weights, view, grades, crash, crash_note))
            except ValueError as exc:
                rows.append(Row(name, dict(weights), error=str(exc)))
        _logger.info("what-if: %d vectors compared", len(rows))
        return Comparison(tuple(rows), f"{book.source} history, {book.benchmark} benchmark, "
                                       f"{book.lookback}-day lookback, cash at {book.rate:.2%}")

    def _crash(self, view: PortfolioView) -> tuple[CrashStress | None, str]:
        """A2: the vector's β (W2's, with its Newey–West error; the risk
        model's without one) through Ken French's daily market. None, with
        how to get it, when the series isn't stored."""
        if self._external is None:
            return None, "Crash stress needs Ken French's daily market, and no series store is wired."
        try:
            market = self._external.series(*CRASH_MARKET).values
            bills = self._external.series(*CRASH_BILLS).values
        except LookupError as exc:
            return None, f"Crash stress needs Ken French's daily market: {exc}."
        if view.regressions is not None:
            beta, error = view.regressions.beta(PORTFOLIO_ROW)
        elif view.report.beta is not None:
            beta, error = view.report.beta, None
        else:
            return None, "No β to replay: the vector has no benchmark history."
        try:
            return crash_stress(market, bills, beta, error), ""
        except ValueError as exc:
            return None, f"The crash replay failed: {exc}."

    # --------------------------------------------------------- named sets
    def saved(self) -> dict[str, dict[str, float]]:
        state = self._state.load(STATE_KEY)
        return {name: {s: float(w) for s, w in weights.items()}
                for name, weights in state.get("sets", {}).items()}

    def save(self, name: str, weights: Mapping[str, float]) -> None:
        name = name.strip()
        if not name:
            raise ValueError("a saved vector needs a name")
        sets = self.saved()
        sets[name] = W.validate(weights)
        self._state.save(STATE_KEY, {"sets": sets})

    def delete(self, name: str) -> None:
        sets = self.saved()
        sets.pop(name, None)
        self._state.save(STATE_KEY, {"sets": sets})
