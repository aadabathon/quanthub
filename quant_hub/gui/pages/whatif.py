"""What-if: weight vectors through Portfolio's analysis and your rules (ALLOCATION A3, D73).

The page on ``services/whatif.py`` (D72). Nothing here trades, and no
vector is ever stored as a positions snapshot.

**The side panel builds vectors:**
- **Typed:** one holding a line ("SPY 60"), with a name, added to the
  comparison or saved as a named set.
- **Built:**
  - today
  - today's picks scaled to a total
  - an index and cash
  - today's picks netted into the core at a target β
  - the four references on today's names: minimum variance, equal risk,
    equal weight, and max Sharpe, labelled in-sample
- **Saved sets** load back into the comparison, or are deleted.

**The main column compares them:**
- **The vectors:** each one's holdings and cash. Select one to remove it.
- **The comparison:** one column per vector:
  - β ± SE, vol, VaR 95 and ES 99 (a day)
  - the lookback's drawdown, and A2's crash stress with its band
  - N_eff (looked through the core where it can be)
  - the top risk share, growth, and the fails and warns under your
    sections 3 and 4
- **Risk shares:** each name's share of each vector's volatility.
- **Notes:** the analysis settings (today's source, benchmark, lookback
  and cash rate), any vector that couldn't run and why, and max Sharpe's
  in-sample warning whenever it's compared.

The context loads when the page opens: today's book needs REGIME.md's
section 2 and positions, and the page says what's missing otherwise. The
comparison is one TaskRunner job.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt

from quant_hub.gui.components import (
    Card,
    CardGrid,
    DataTable,
    Field,
    Note,
    PageHeader,
    SelectField,
    TextArea,
    button,
    form_grid,
)
from quant_hub.gui.pages.base import ScrollPage, saved_grid
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
from quant_hub.models import whatif as W
from quant_hub.services.whatif import MAX_SHARPE, Comparison, Context, Row, WhatIfService

TODAY, PICKS, INDEX, NETTED, REFS = ("Today", "Today, picks to a total", "Index and cash",
                                     "Picks netted into the core", "The four references")
_BUILDERS = (TODAY, PICKS, INDEX, NETTED, REFS)
_PARAMS = {PICKS: ("Picks total %", "20"), INDEX: ("Index %", "70"),
           NETTED: ("Target β", "0.90")}
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2
_FULL = 2 * _HALF + SPACE_M
_NOTE = SIDE_PANEL_WIDTH - 2 * SPACE_L
_ROW = 32
_NO_SET = "(no saved sets)"
_METRICS = ("β ± SE", "Vol a year", "VaR 95, a day", "ES 99, a day", "Drawdown, lookback",
            "Crash stress", "Effective holdings", "Top risk share", "Growth a year", "Fails",
            "Warns")


def summary(weights: dict[str, float], limit: int = 4) -> str:
    """"SPY 60%, QQQ 10%, … cash 30%"."""
    parts = [f"{s} {w:.0%}" for s, w in sorted(weights.items(), key=lambda kv: -kv[1])]
    shown = parts[:limit] + ([f"+{len(parts) - limit} more"] if len(parts) > limit else [])
    return ", ".join(shown + [f"cash {W.cash(weights):.0%}"])


def column(row: Row) -> list:
    """One vector's comparison column, in ``_METRICS`` order."""
    if row.view is None:
        return [("—", PALETTE.muted)] * len(_METRICS)
    r, g = row.view.report, row.graded
    beta = row.beta
    crash = g.crash if g else None
    if crash is not None:
        stress = f"{crash.worst.depth:.0%}"
        if crash.band:
            low, high = sorted(d for _, d in crash.band)
            stress += f" ({low:.0%}–{high:.0%})"
    else:
        stress = "—"
    top = row.top_risk
    growth = row.view.growth.growth if row.view.growth is not None else None
    effective = g.effective if g else r.n_effective
    fails, warns = row.fails, row.warns
    return [
        (f"{beta[0]:.2f} ± {beta[1]:.2f}" if beta and beta[1] is not None
         else f"{beta[0]:.2f}" if beta else "—"),
        f"{r.vol_annual:.1%}",
        f"{r.var_historical[0.95] / r.equity:.2%}",
        f"{r.es_historical[0.99] / r.equity:.2%}",
        f"{-float(r.drawdown.min()):.1%}",
        stress,
        f"{effective:.1f}" if effective is not None else "—",
        f"{top[0]} {top[1]:.0%}" if top else "—",
        f"{growth:+.1%}" if growth is not None and np.isfinite(growth) else "—",
        (str(fails), PALETTE.critical if fails else PALETTE.good),
        (str(warns), PALETTE.warning if warns else PALETTE.good),
    ]


class WhatIfPage(ScrollPage):
    def __init__(self, whatif: WhatIfService, tasks, state_store) -> None:
        super().__init__()
        self._whatif, self._tasks = whatif, tasks
        self._ctx: Context | None = None
        self._vectors: dict[str, dict[str, float]] = {}
        self._last: Comparison | None = None
        self._saved: dict[str, dict[str, float]] = {}
        self._loading = False
        self._header = PageHeader("What-if", "Weight vectors through Portfolio's analysis "
                                             "and your rules; nothing here trades")
        self.body.addWidget(self._header)
        grid = CardGrid()
        grid.add_column([("vectors", self._build_vectors(), None),
                         ("comparison", self._build_comparison(), None),
                         ("risk", self._build_risk(), None)], x=0, w=8)
        grid.add_column([("build", self._build_editor(), None),
                         ("sets", self._build_sets(), None)], x=8, w=4,
                        min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, "whatif.cards")
        self.body.addWidget(grid, 1)

    # ------------------------------------------------------------ building
    def _build_vectors(self) -> Card:
        card = Card("The vectors", "Each one's holdings; the rest is cash")
        self._list = DataTable([("Vector", "left"), ("Holdings", "left")], stretch=1,
                               row_height=_ROW)
        self._list.setMinimumHeight(8 * _ROW + 40)
        card.body.addWidget(self._list, 1)
        card.add_action(button("Remove", icon_name="ph.trash", on_click=self._remove))
        self._compare_button = button("Compare", "primary", "ph.play", on_click=self._compare)
        card.add_action(self._compare_button)
        return card

    def _build_comparison(self) -> Card:
        card = Card("The comparison", "Each vector through Portfolio's analysis, your sections "
                                      "3 and 4, and A2's crash stress")
        self._table = DataTable([("Measure", "left")], row_height=_ROW)
        self._table.setMinimumHeight(len(_METRICS) * _ROW + 56)
        # One column per vector: more than fit the card scroll sideways.
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        card.body.addWidget(self._table)
        self._sharpe_note = Note("ph.warning", "warning")
        self._sharpe_note.set_text(
            f"{MAX_SHARPE} is fitted to the lookback it's judged on, so it looks better here "
            "than it will. It loads on whatever did well lately: the estimation error in the "
            "means dominates it (Michaud 1989). It's here as a lesson, not a target.")
        self._sharpe_note.hide()
        card.body.addWidget(self._sharpe_note)
        self._notes = Note("ph.info", "info")
        self._notes.hide()
        card.body.addWidget(self._notes)
        return card

    def _build_risk(self) -> Card:
        card = Card("Risk shares", "Each name's share of each vector's volatility")
        self._risk = DataTable([("Name", "left")], row_height=_ROW)
        self._risk.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._risk.setMinimumHeight(160)
        card.body.addWidget(self._risk, 1)
        return card

    def _build_editor(self) -> Card:
        card = Card("Build a vector", "Type one holding a line, or start from a builder")
        self._name = Field("Name", "My mix", _FULL)
        self._editor = TextArea(placeholder="SPY 60\nIEF 20\n(the rest is cash)")
        self._editor.setMinimumHeight(120)
        card.body.addLayout(form_grid([self._name], 1))
        card.body.addWidget(self._editor)
        card.body.addWidget(button("Add to the comparison", icon_name="ph.plus",
                                   on_click=self._add_typed))
        self._builder = SelectField("Builder", list(_BUILDERS), TODAY, _FULL)
        self._builder.select.current_changed.connect(self._on_builder)
        self._params = {b: Field(caption, value, _HALF) for b, (caption, value) in _PARAMS.items()}
        card.body.addLayout(form_grid([self._builder], 1))
        card.body.addLayout(form_grid(list(self._params.values()), 2))
        card.body.addWidget(button("Build and add", icon_name="ph.plus-circle", on_click=self._build))
        self._context_note = Note("ph.info", "info", width=_NOTE)
        self._context_note.hide()
        card.body.addWidget(self._context_note)
        self._on_builder(TODAY)
        return card

    def _build_sets(self) -> Card:
        card = Card("Saved sets", "Named vectors, kept in your local store")
        self._sets = SelectField("Set", [_NO_SET], _NO_SET, _FULL)
        card.body.addLayout(form_grid([self._sets], 1))
        row = form_grid([button("Load", icon_name="ph.download-simple", on_click=self._load_set),
                         button("Save typed", icon_name="ph.floppy-disk", on_click=self._save_set),
                         button("Delete", icon_name="ph.trash", on_click=self._delete_set)], 3)
        card.body.addLayout(row)
        return card

    # ------------------------------------------------------------- context
    def on_show(self) -> None:
        if self._ctx is None and not self._loading:
            self._loading = True
            self._header.set_status("Reading today's book: your rules, positions and history…")
            self._tasks.submit(self._whatif.context, on_done=self._on_context,
                               on_error=self._on_context_error)
        self._refresh_sets()

    def _on_context(self, ctx: Context) -> None:
        self._loading = False
        self._ctx = ctx
        self._context_note.set_text(f"Every vector is analysed as today's book is: {ctx.source} "
                                    f"history, {ctx.benchmark} as the benchmark, a "
                                    f"{ctx.lookback}-day lookback, cash at {ctx.rate:.2%}.")
        self._context_note.setVisible(True)
        if not self._vectors:
            self._put(TODAY, ctx.today)
        self._header.set_status("Today's book is loaded. Add vectors, then Compare.")

    def _on_context_error(self, exc: Exception) -> None:
        self._loading = False
        self._header.set_status(f"Today's book can't be read: {exc}", error=True)

    # ------------------------------------------------------------ vectors
    def _put(self, name: str, weights: dict[str, float]) -> None:
        self._vectors[name] = dict(weights)
        self._list.set_rows([[n, summary(w)] for n, w in self._vectors.items()])

    def _remove(self) -> None:
        row = self._list.selected_row()
        if 0 <= row < len(self._vectors):
            del self._vectors[list(self._vectors)[row]]
            self._list.set_rows([[n, summary(w)] for n, w in self._vectors.items()])

    def _typed(self) -> tuple[str, dict[str, float]] | None:
        try:
            weights = W.parse(self._editor.toPlainText())
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return None
        name = self._name.text().strip()
        if not name or not weights:
            self._header.set_status("A vector needs a name and at least one holding.", error=True)
            return None
        return name, weights

    def _add_typed(self) -> None:
        typed = self._typed()
        if typed:
            self._put(*typed)
            self._header.set_status(f"Added {typed[0]}.")

    def _on_builder(self, builder: str) -> None:
        """Show only the chosen builder's parameter."""
        for name, field in self._params.items():
            field.setVisible(name == builder)

    def _build(self) -> None:
        ctx = self._ctx
        if ctx is None:
            self._header.set_status("Today's book isn't loaded yet.", error=True)
            return
        builder = self._builder.current()
        try:
            if builder == TODAY:
                self._put(TODAY, ctx.today)
            elif builder == PICKS:
                total = self._params[PICKS].number(scale=0.01)
                self._put(f"Picks at {total:.0%}", self._whatif.scaled(ctx, total))
            elif builder == INDEX:
                share = self._params[INDEX].number(scale=0.01)
                self._put(f"{ctx.benchmark} {share:.0%}", W.index_and_cash(ctx.benchmark, share))
            elif builder == NETTED:
                target = self._params[NETTED].number()
                self._put(f"Netted at β {target:.2f}", self._whatif.netted(ctx, target))
            else:
                self._header.set_status("Fitting the references on today's names…")
                self._tasks.submit(lambda: self._whatif.references(ctx),
                                   on_done=self._on_references, on_error=self._on_error)
                return
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        self._header.set_status(f"Added {list(self._vectors)[-1]}.")

    def _on_references(self, refs: dict[str, dict[str, float]]) -> None:
        for name, weights in refs.items():
            self._put(name, weights)
        self._header.set_status(f"Added the {len(refs)} references.")

    # ------------------------------------------------------------ compare
    def _compare(self) -> None:
        if self._ctx is None or not self._vectors:
            self._header.set_status("Load today's book and add at least one vector first.",
                                    error=True)
            return
        ctx, vectors = self._ctx, dict(self._vectors)
        self._compare_button.setEnabled(False)
        self._header.set_status(f"Comparing {len(vectors)} vectors…")
        self._tasks.submit(lambda: self._whatif.compare(ctx, vectors), on_done=self._render,
                           on_error=self._on_error)

    def _on_error(self, exc: Exception) -> None:
        self._compare_button.setEnabled(True)
        self._header.set_status(str(exc), error=True)

    def _render(self, comparison: Comparison) -> None:
        self._compare_button.setEnabled(True)
        self._last = comparison
        rows = comparison.rows
        self._table.set_columns([("Measure", "left"), *((r.name, "right") for r in rows)])
        columns = [column(r) for r in rows]
        self._table.set_rows([[metric, *(c[i] for c in columns)]
                              for i, metric in enumerate(_METRICS)])
        names = sorted({row.symbol for r in rows if r.view for row in r.view.rows})
        shares = [{row.symbol: row.risk_share for row in r.view.rows} if r.view else {}
                  for r in rows]
        self._risk.set_columns([("Name", "left"), *((r.name, "right") for r in rows)])
        self._risk.set_rows([[n, *(f"{s[n]:.0%}" if n in s else "" for s in shares)]
                             for n in names])
        self._sharpe_note.setVisible(any(r.name == MAX_SHARPE for r in rows))
        notes = [f"{r.name}: {r.error}" for r in rows if r.error]
        notes += [f"{r.name}: {line}" for r in rows if r.view for line in r.view.left_out]
        # Why a measure is blank, said once rather than per vector.
        for why in dict.fromkeys(g.crash_note for r in rows if (g := r.graded) and g.crash_note):
            notes.append(why)
        for why in dict.fromkeys(g.effective_note for r in rows
                                 if (g := r.graded) and g.effective_note):
            notes.append(f"Effective holdings: {why}.")
        self._notes.set_text(" ".join([comparison.context_note + "."] + notes))
        self._notes.setVisible(True)
        ran = sum(r.view is not None for r in rows)
        self._header.set_status(f"Compared {ran} of {len(rows)} vectors.")

    # ---------------------------------------------------------------- sets
    def _refresh_sets(self) -> None:
        self._tasks.submit(self._whatif.saved, on_done=self._on_sets, on_error=self._on_error)

    def _on_sets(self, sets: dict[str, dict[str, float]]) -> None:
        self._saved = sets
        names = list(sets) or [_NO_SET]
        self._sets.select.set_items(names, names[0])

    def _load_set(self) -> None:
        name = self._sets.current()
        if name in self._saved:
            self._put(name, self._saved[name])
            self._header.set_status(f"Added {name} from your saved sets.")

    def _save_set(self) -> None:
        typed = self._typed()
        if typed:
            name, weights = typed
            self._tasks.submit(lambda: self._whatif.save(name, weights),
                               on_done=lambda _r: self._refresh_sets(), on_error=self._on_error)
            self._header.set_status(f"Saved {name}.")

    def _delete_set(self) -> None:
        name = self._sets.current()
        if name in self._saved:
            self._tasks.submit(lambda: self._whatif.delete(name),
                               on_done=lambda _r: self._refresh_sets(), on_error=self._on_error)
            self._header.set_status(f"Deleted {name}.")
