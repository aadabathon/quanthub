"""The What-if page's job: vectors through Portfolio's analysis and your rules (ALLOCATION A3, D72).

A vector (``models/whatif.py``) is a set of weights, the rest cash. It's
never stored as a positions snapshot, which ALLOCATION finding 5 asked
for. Named sets are kept in the view-state store (decision 5).

**The context** (``context``): today's book as the review analyses it
(``ReviewService.current_view``). That fixes the analysis every vector
gets: REGIME.md's history source, benchmark, lookback and cash rate, and
its sleeves. The context also carries:
- today's vector: each holding's market value over equity (stocks and
  funds; options aren't vector weights)
- each name's β, for the netting builder
- the core, from section 3
- the lookback's daily returns, for the references

**The builders** (``models/whatif.py``): today; today with the picks
scaled to a total; an index and cash; and today's picks netted into the
core at a target β. Plus the references on today's names and invested
share: minimum variance, equal risk, equal weight, and maximum Sharpe,
in-sample and labelled so.

**The comparison** (``compare``), one TaskRunner job. Each vector gets:
- ``PortfolioService.analyse``, Portfolio's own analysis
- ``ReviewService.grade``: sections 3 and 4, A1-aware, as measured
- A2's crash stress

The table reads off each: β ± SE, vol, VaR, ES, the lookback's drawdown,
the crash stress, N_eff, the top risk share, growth, and the fails and
warns.

**Missing bars are named, never fetched behind your back** (V0's offline
rule): the analysis reads through the history source as the review does,
and a vector whose names have no history says so.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from quant_hub.data.regime import Grade, RuleSet
from quant_hub.models import whatif as W
from quant_hub.services.portfolio import PORTFOLIO_ROW, PortfolioService, PortfolioView
from quant_hub.services.review import DEFAULT_BENCHMARK, DEFAULT_LOOKBACK, Graded, ReviewService
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

STATE_KEY = "whatif.sets"
MAX_SHARPE = "Max Sharpe (in-sample)"
REFERENCES = ("Minimum variance", "Equal risk", "Equal weight", MAX_SHARPE)


@dataclass(frozen=True, eq=False)
class Context:
    """What every vector is analysed against (module docstring)."""

    rules: RuleSet
    source: str
    benchmark: str
    lookback: int
    rate: float
    today: dict[str, float]
    betas: dict[str, float]
    core: frozenset[str]
    returns: pd.DataFrame | None
    as_of: datetime


@dataclass(frozen=True, eq=False)
class Row:
    """One vector, analysed and graded."""

    name: str
    weights: dict[str, float]
    view: PortfolioView | None = None
    graded: Graded | None = None
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
    def fails(self) -> int:
        return self.graded.count(Grade.FAIL) if self.graded else 0

    @property
    def warns(self) -> int:
        return self.graded.count(Grade.WARN) if self.graded else 0


@dataclass(frozen=True)
class Comparison:
    rows: tuple[Row, ...]
    context_note: str = ""
    notes: tuple[str, ...] = field(default=())


class WhatIfService:
    def __init__(self, portfolio: PortfolioService, review: ReviewService,
                 state: ViewStateStore, now: Callable[[], datetime] = datetime.now) -> None:
        self._portfolio = portfolio
        self._review = review
        self._state = state
        self._now = now

    # ------------------------------------------------------------ context
    def context(self) -> Context:
        """Today's book and the analysis settings (module docstring).
        Raises ``ReviewError`` naming what REGIME.md or the positions lack."""
        as_of = self._now()
        view, rate = self._review.current_view(now=as_of)
        rules, _, _ = self._review.load_rules()
        settings = rules.settings
        equity = view.report.equity
        today = W.validate({row.symbol: row.value / equity for row in view.rows if row.value > 0})
        betas = {row.symbol: row.beta for row in view.rows if row.beta is not None}
        returns = view.returns
        return Context(rules, settings.history_source or "", settings.benchmark or DEFAULT_BENCHMARK,
                       settings.lookback_days or DEFAULT_LOOKBACK, rate, today, betas, rules.core,
                       returns, as_of)

    # ----------------------------------------------------------- builders
    def scaled(self, ctx: Context, total: float) -> dict[str, float]:
        return W.scale_picks(ctx.today, ctx.core, total)

    def netted(self, ctx: Context, target: float, core: str | None = None) -> dict[str, float]:
        """Today's picks, and the core at the weight that brings the book to
        ``target``. ``core`` defaults to section 3's (one name), else the
        benchmark."""
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
        """Every vector through the analysis and the rules (module docstring)."""
        sleeves = {row.symbol: row.sleeve for row in ctx.rules.sleeves if row.sleeve}
        rows = []
        for name, weights in vectors.items():
            try:
                weights = W.validate(weights)
                snapshot = W.to_snapshot(weights, name, ctx.as_of)
                view = self._portfolio.analyse(snapshot, None, ctx.source, ctx.benchmark,
                                               ctx.lookback, ctx.rate, now=ctx.as_of,
                                               sleeves=sleeves)
                rows.append(Row(name, weights, view, self._review.grade(view, snapshot, ctx.rules)))
            except ValueError as exc:
                rows.append(Row(name, dict(weights), error=str(exc)))
        _logger.info("what-if: %d vectors compared", len(rows))
        return Comparison(tuple(rows), f"{ctx.source} history, {ctx.benchmark} benchmark, "
                                       f"{ctx.lookback}-day lookback, cash at {ctx.rate:.2%}")

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
