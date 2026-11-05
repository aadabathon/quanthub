"""Hedge Lab: puts, collars, trend and cash on the book, against every crash (HEDGE U4, D71).

HEDGE_PLAN's page, on ``services/hedge.py`` (U3) and ``models/hedge.py``
(U1). It doesn't pick a hedge: it lays every candidate on one yardstick,
and you choose.

**The side panel:**
- **The book:** the β proxy on French's market since 1926, or today's mix
  as the review analyses it.
  - β defaults to your newest review's (A2), else 1.
  - The smile is today's surface, or flat beside it (decision 5).
  - Extra holdings to try as hedges (a Treasury fund, a trend fund) are
    read from stored bars.
  - The book's value, if given, turns each option hedge into whole
    contracts.
- **The selected hedge:** Δ log growth ± SE, the cost in calm times, what
  it saved per crash, drawdowns, its up and down betas. An option program
  also shows its premiums and payoffs, today's price with its percentile
  against history, and whole contracts.

**The main column:**
- **Every hedge:** one row per candidate and size, in decision 2's order.
  Candidates that can't run (no VIX yet, say) are listed under it with
  why. Selecting a row selects the hedge everywhere.
- **The frontier:** cost a year (x) against the average crash loss saved
  (y), one dot per row. Dominated dots are dimmed. Nothing is optimized:
  selection flatters the best-looking dot.
- **Growth and drawdown:** the book unhedged and hedged, on the hedge's
  window. Growth is Σ ln(1 + r), so a century stays readable.
- **The crash table:** each crash window (the market down 15% or more,
  by rule), with the book's return over it unhedged and hedged, and what
  the hedge paid. It's for the selected hedge: one column per candidate
  wouldn't fit the window.

One TaskRunner job runs everything: a minute at most on a century, most
of it the option programs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtWidgets import QHBoxLayout, QWidget

from quant_hub.gui.charts import Curve, CurveChart, Panel, Series, StackedTimeSeries
from quant_hub.gui.components import (
    Card,
    CardGrid,
    DataTable,
    Field,
    Note,
    PageHeader,
    SelectField,
    StatTable,
    button,
    form_grid,
    label,
)
from quant_hub.gui.pages.base import ScrollPage, saved_grid
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
from quant_hub.models import hedge as H
from quant_hub.services.hedge import (
    SIZES,
    HedgeDataError,
    HedgeRun,
    HedgeService,
    Result,
    Shape,
    TodayCost,
    v1_candidates,
)
from quant_hub.services.review import ReviewService

PROXY, MIX = "β proxy since 1926", "Today's mix"
SURFACE, FLAT = "Today's surface", "Flat smile"
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2
_FULL = 2 * _HALF + SPACE_M
_NOTE = SIDE_PANEL_WIDTH - 2 * SPACE_L
_ROW = 32


@dataclass(frozen=True)
class _Outcome:
    run: HedgeRun
    shape: Shape
    today: dict[str, TodayCost]
    value: float | None


def _pct(x: float | None, signed: bool = False) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


class HedgeLabPage(ScrollPage):
    def __init__(self, hedge: HedgeService, review: ReviewService, tasks, state_store) -> None:
        super().__init__()
        self._hedge, self._review, self._tasks = hedge, review, tasks
        self._outcome: _Outcome | None = None
        self._rows: list[Result] = []
        self._asked_beta = False
        self._header = PageHeader("Hedge Lab", "Puts, collars, trend and cash on your book, "
                                               "against every crash since 1926")
        self.body.addWidget(self._header)
        grid = CardGrid()
        grid.add_column([("hedges", self._build_hedges(), None),
                         ("frontier", self._build_frontier(), None),
                         ("growth", self._build_growth(), None),
                         ("crashes", self._build_crashes(), None)], x=0, w=8)
        grid.add_column([("book", self._build_book(), None),
                         ("hedge", self._build_selected(), None)], x=8, w=4,
                        min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, "hedge_lab.cards")
        self.body.addWidget(grid, 1)

    # ------------------------------------------------------------ building
    def _build_book(self) -> Card:
        card = Card("The book", "What's hedged: the whole book's β (decision 8)")
        self._book = SelectField("Book", [PROXY, MIX], PROXY, _FULL)
        self._beta = Field("β", "1.00", _HALF)
        self._smile = SelectField("Smile", [SURFACE, FLAT], SURFACE, _HALF)
        self._streams = Field("Also try holding", "", _FULL, placeholder="symbols, e.g. IEF",
                              uppercase=True)
        self._value = Field("Book value $", "", _HALF, placeholder="optional")
        card.body.addLayout(form_grid([self._book], 1))
        card.body.addLayout(form_grid([self._beta, self._smile], 2))
        card.body.addLayout(form_grid([self._streams], 1))
        card.body.addLayout(form_grid([self._value], 2))
        self._shape_note = Note("ph.info", "info", width=_NOTE)
        self._shape_note.hide()
        card.body.addWidget(self._shape_note)
        self._run_button = button("Run every hedge", "primary", "ph.play", on_click=self._run)
        card.body.addWidget(self._run_button)
        return card

    def _build_selected(self) -> Card:
        self._selected_card = Card("The selected hedge", "Select a row in Every hedge")
        self._stats = StatTable([
            ("Growth", [("growth", "Δ log growth a year"), ("cost", "Cost a year, calm days"),
                        ("saved", "Saved per crash, on average")]),
            ("Falls", [("deepest", "Deepest fall"), ("year", "Worst 12 months")]),
            ("Convexity", [("down", "β in down months"), ("up", "β in up months")]),
            ("The program", [("premium", "Premiums a year"), ("payoff", "Payoffs a year"),
                             ("spread", "Half-spreads a year"), ("rolls", "Rolls"),
                             ("today", "Today's cost a year"), ("percentile", "Percentile since"),
                             ("contracts", "Whole contracts")]),
        ])
        self._selected_card.body.addWidget(self._stats)
        return self._selected_card

    def _build_hedges(self) -> Card:
        card = Card("Every hedge", "Each candidate at 25, 50 and 100%, on the days it has data for")
        self._table = DataTable([("Hedge", "left"), ("Size", "right"), ("Since", "left"),
                                 ("Δ growth", "right"), ("Cost", "right"), ("Saved", "right"),
                                 ("Deepest fall", "right")], stretch=0, row_height=_ROW)
        self._table.setMinimumHeight(380)
        self._table.selection_changed.connect(self._select)
        card.body.addWidget(self._table, 1)
        self._missing = Note("ph.info", "info")
        self._missing.hide()
        card.body.addWidget(self._missing)
        return card

    def _build_frontier(self) -> Card:
        card = Card("The frontier", "Cost a year against the average crash loss saved; "
                                    "dominated hedges dimmed")
        self._frontier = CurveChart(x_axis="percent", y_axis="percent", x_name="cost",
                                    x_fmt="{:.1%}")
        self._frontier.setMinimumHeight(240)
        card.body.addWidget(self._frontier)
        return card

    def _build_growth(self) -> Card:
        self._growth_card = Card("Growth and drawdown", "The book, unhedged and hedged")
        self._growth = StackedTimeSeries()
        self._growth.setMinimumHeight(320)
        self._growth_card.body.addWidget(self._growth)
        return self._growth_card

    def _build_crashes(self) -> Card:
        self._crash_card = Card("The crashes", "Each window the market fell 15% or more, by rule")
        self._crashes = DataTable([("Peak", "left"), ("Trough", "left"), ("Market", "right"),
                                   ("Unhedged", "right"), ("Hedged", "right"), ("Paid", "right")],
                                  row_height=_ROW)
        self._crashes.balance_columns()
        self._crashes.setMinimumHeight(200)
        self._crash_card.body.addWidget(self._crashes, 1)
        return self._crash_card

    # ------------------------------------------------------------ running
    def on_show(self) -> None:
        if not self._asked_beta:
            self._asked_beta = True
            self._tasks.submit(self._review.latest_beta, on_done=self._default_beta,
                               on_error=lambda _exc: None)

    def _default_beta(self, beta: float | None) -> None:
        if beta is not None and self._beta.text() == "1.00":
            self._beta.set_text(f"{beta:.2f}")

    def _run(self) -> None:
        try:
            beta = self._beta.number()
            value = self._value.number() if self._value.text().strip() else None
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        streams = tuple(s for s in self._streams.text().replace(",", " ").split() if s)
        kind, flat = self._book.current(), self._smile.current() == FLAT
        self._run_button.setEnabled(False)
        self._header.set_status("Running every hedge on the book: about a minute on a century…")
        self._tasks.submit(lambda: self._job(kind, beta, flat, streams, value),
                           on_done=self._render, on_error=self._error)

    def _job(self, kind: str, beta: float, flat: bool, streams: tuple[str, ...],
             value: float | None) -> _Outcome:
        """Worker thread: the book, today's shape, every hedge, today's prices."""
        if kind == PROXY:
            book = self._hedge.proxy_book(beta)
        else:
            view, rate = self._review.current_view()
            book = self._hedge.mix_book(view, rate)
        shape = self._hedge.shape(flat=flat)
        candidates = v1_candidates(streams)
        run = self._hedge.run(book, candidates, SIZES, shape)
        today = {}
        for candidate in candidates:
            if candidate.program is not None and not candidate.program.collar:
                try:
                    today[candidate.name] = self._hedge.today(book, candidate, shape)
                except (HedgeDataError, ValueError):
                    pass
        return _Outcome(run, shape, today, value)

    def _error(self, exc: Exception) -> None:
        self._run_button.setEnabled(True)
        self._header.set_status(str(exc), error=True)

    # ---------------------------------------------------------- rendering
    def _render(self, outcome: _Outcome) -> None:
        self._run_button.setEnabled(True)
        self._outcome = outcome
        run = outcome.run
        self._rows = [r for r in run.results if r.assessment is not None]
        self._table.set_rows([[r.candidate.name, f"{r.size:.0%}", str(r.first.year),
                               f"{r.assessment.growth:+.2%} ± {r.assessment.growth_error:.2%}",
                               _pct(r.assessment.cost), _pct(r.assessment.saved, True),
                               _pct(r.assessment.drawdown[1])] for r in self._rows])
        missing: dict[str, list[str]] = {}
        for r in run.results:
            if r.assessment is None and r.size == SIZES[-1]:
                missing.setdefault(r.missing, []).append(r.candidate.name)
        self._missing.set_text(" ".join(f"Not run: {', '.join(names)}. {why}."
                                        for why, names in missing.items()))
        self._missing.setVisible(bool(missing))
        self._shape_note.set_text(f"Option marks: {outcome.shape.label}.")
        self._shape_note.setVisible(True)
        self._draw_frontier(selected=None)
        self._header.set_status(f"{len(self._rows)} hedges on {run.book} (β {run.beta:.2f}). "
                                "Select one to see it against the book.")
        if self._rows:
            self._table.selectRow(0)

    def _draw_frontier(self, selected: int | None) -> None:
        if not self._rows:
            self._frontier.set_curves([])
            return
        costs = np.array([r.assessment.cost for r in self._rows])
        saved = np.array([r.assessment.saved if r.assessment.saved is not None else np.nan
                          for r in self._rows])
        usable = np.isfinite(costs) & np.isfinite(saved)
        dominated = np.zeros(len(costs), dtype=bool)
        dominated[usable] = H.dominated(costs[usable], saved[usable])
        curves = []
        for mask, color, name in ((usable & ~dominated, PALETTE.series, "on the frontier"),
                                  (usable & dominated, PALETTE.muted, "dominated")):
            if mask.any():
                order = np.argsort(costs[mask])
                curves.append(Curve(name, costs[mask][order], saved[mask][order], color,
                                    marker="o", line=False, readout=False, fmt="{:.1%}"))
        if selected is not None and usable[selected]:
            curves.append(Curve(self._rows[selected].candidate.name, costs[[selected]],
                                saved[[selected]], PALETTE.series_2, marker="d", line=False,
                                readout=False, fmt="{:.1%}"))
        self._frontier.set_curves(curves, zero_line=True)

    def _select(self, row: int) -> None:
        if not 0 <= row < len(self._rows) or self._outcome is None:
            return
        r = self._rows[row]
        a = r.assessment
        self._draw_frontier(row)
        self._selected_card.set_subtitle(f"{r.candidate.name} at {r.size:.0%}, "
                                         f"{r.first} to {r.last}")
        stats = self._stats
        stats.set_value("growth", f"{a.growth:+.2%} ± {a.growth_error:.2%}")
        stats.set_value("cost", f"{a.cost:+.2%} ± {a.cost_error:.2%}")
        stats.set_value("saved", _pct(a.saved, True))
        stats.set_value("deepest", f"{a.drawdown[0]:.1%} → {a.drawdown[1]:.1%}")
        stats.set_value("year", f"{_pct(a.worst_year[0], True)} → {_pct(a.worst_year[1], True)}")
        stats.set_value("down", f"{a.beta_down:+.2f} ± {a.beta_down_error:.2f}")
        stats.set_value("up", f"{a.beta_up:+.2f} ± {a.beta_up_error:.2f}")
        program = r.program
        for key in ("premium", "payoff", "spread", "rolls", "today", "percentile", "contracts"):
            stats.set_value(key, "—")
        if program is not None:
            stats.set_value("premium", _pct(program.premium))
            stats.set_value("payoff", _pct(program.payoff))
            stats.set_value("spread", _pct(program.spread))
            stats.set_value("rolls", f"{program.rolls:,}")
        today = self._outcome.today.get(r.candidate.name)
        if today is not None:
            stats.set_value("today", _pct(today.annual * r.size * self._outcome.run.beta))
            if today.percentile is not None:
                stats.set_value("percentile", f"{today.percentile:.0%} since {today.first.year}")
        shape, value = self._outcome.shape, self._outcome.value
        if program is not None and value and shape.spot:
            n = H.contracts(r.size, self._outcome.run.beta, value, shape.spot)
            stats.set_value("contracts", f"{n.whole} {shape.symbol} (ideal {n.ideal:.2f}; "
                                         f"h {n.h_effective:.0%})")
        self._draw_growth(r)
        self._fill_crashes(r)

    def _draw_growth(self, r: Result) -> None:
        dates = r.hedged.index
        unhedged = np.cumsum(np.log1p(r.unhedged.to_numpy()))
        hedged = np.cumsum(np.log1p(r.hedged.to_numpy()))

        def drawdown(levels: np.ndarray) -> np.ndarray:
            return np.exp(levels - np.maximum.accumulate(np.maximum(levels, 0.0))) - 1.0
        self._growth.set_panels(dates, [
            Panel("Growth, Σ ln(1 + r)", [Series("Unhedged", unhedged, PALETTE.muted, fmt="{:+.2f}"),
                                          Series("Hedged", hedged, PALETTE.series, fmt="{:+.2f}")],
                  axis="number", stretch=2),
            Panel("Drawdown", [Series("Unhedged", drawdown(unhedged), PALETTE.muted, fmt="{:.1%}"),
                               Series("Hedged", drawdown(hedged), PALETTE.series, fmt="{:.1%}")],
                  axis="percent")])
        self._growth_card.set_subtitle(f"The book unhedged and with {r.candidate.name} at "
                                       f"{r.size:.0%}")

    def _fill_crashes(self, r: Result) -> None:
        rows = [[c.window.peak.isoformat(), c.window.trough.isoformat(),
                 _pct(-c.window.depth, True), _pct(c.unhedged, True), _pct(c.hedged, True),
                 (_pct(c.paid, True), PALETTE.good if c.paid > 0 else PALETTE.text_secondary)]
                for c in r.assessment.crashes]
        self._crashes.set_rows(rows or [[("No crash in this window", PALETTE.muted),
                                         "", "", "", "", ""]])
        self._crash_card.set_subtitle(f"{len(rows)} windows, each the market down 15% or more; "
                                      f"{r.candidate.name} at {r.size:.0%}")
