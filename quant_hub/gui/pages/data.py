"""Data: everything the app has accumulated, where it came from, how fresh (D64).

D59's direction is to accumulate free data. This page shows what the store
holds, one card per kind:

* **Outside series** (D60, D61, D69): Ken French's factors, Shiller's
  monthly data and FRED's series, with their unit, range, count and when
  they were fetched.
  Selecting one previews it in the side panel with its citation, which the
  source's terms ask to be shown.
* **Fund holdings** (D62): the newest N-PORT report per fund.
* **Option chains** (D63): the snapshots per symbol, and the watchlist that
  collects them daily.
* **Price history:** the daily bars per symbol, source and basis.

The page's buttons run the same Ctrl+K commands (``Shell.run``), so a
button and the search always do the same thing. Reads are one TaskRunner
job. The worker runs jobs in order, so a refresh queued right after an
action shows its result.

The preview plots a return series (unit ``fraction``) as its cumulative log
return, Σ ln(1 + r), so a century stays readable on a linear axis. Every
other series is plotted as it is.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from quant_hub.gui.charts import Panel, Series, StackedTimeSeries
from quant_hub.gui.components import (
    Card,
    CardGrid,
    DataTable,
    EmptyState,
    Note,
    PageHeader,
    button,
    label,
)
from quant_hub.gui.pages.base import ScrollPage, Shell, saved_grid
from quant_hub.gui.theme import SIDE_PANEL_WIDTH, SPACE_L, SPACE_S

_STATE_KEY = "data"
_NOTE_WIDTH = SIDE_PANEL_WIDTH - 2 * SPACE_L


@dataclass(frozen=True)
class _Overview:
    series: list
    funds: list
    chains: list
    bars: list
    has_identity: bool
    watchlist: tuple[str, ...]
    has_fred_key: bool = True


def _day(moment) -> str:
    return f"{moment:%Y-%m-%d}" if moment else ""


class DataPage(ScrollPage):
    def __init__(self, shell: Shell, external_data, fund_holdings, chains, chain_collector,
                 workstation, tasks, state_store) -> None:
        super().__init__()
        self._shell, self._tasks = shell, tasks
        self._external, self._funds, self._chains = external_data, fund_holdings, chains
        self._collector, self._workstation = chain_collector, workstation
        self._series_rows: list = []
        self._empty_hints: dict[DataTable, QWidget] = {}
        self._header = PageHeader("Data", "What the app has accumulated, where it came from, "
                                          "and how fresh it is")
        self.body.addWidget(self._header)
        grid = CardGrid()
        grid.add_column([("series", self._build_series(), None),
                         ("holdings", self._build_holdings(), None),
                         ("chains", self._build_chains(), None),
                         ("bars", self._build_bars(), None)], x=0, w=8)
        # 8 + 4, as on Review and Valuation: three columns are narrower than the
        # side panel's minimum at common window widths (D56).
        grid.add_column([("preview", self._build_preview(), None)], x=8, w=4,
                        min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, f"{_STATE_KEY}.cards")
        self.body.addWidget(grid, 1)

    # ------------------------------------------------------------ building
    def _table_card(self, title: str, subtitle: str, columns, stretch: int,
                    empty: str) -> tuple[Card, DataTable]:
        card = Card(title, subtitle)
        table = DataTable(columns, stretch=stretch)
        table.setMinimumHeight(180)
        card.body.addWidget(table, 1)
        hint = label(empty, "muted", wrap=True)
        card.body.addWidget(hint)
        self._empty_hints[table] = hint
        return card, table

    def _fill(self, table: DataTable, rows: list) -> None:
        """Rows in, and while there are none, a short table and how to fill it."""
        table.set_rows(rows)
        table.setMaximumHeight(16777215 if rows else 44)
        table.setMinimumHeight(180 if rows else 44)
        self._empty_hints[table].setVisible(not rows)

    def _build_series(self) -> Card:
        card, self._series = self._table_card(
            "Outside series", "Free data stored as fetched; select one to preview it",
            [("Source", "left"), ("Series", "left"), ("Title", "left"), ("Frequency", "left"),
             ("From", "left"), ("Through", "left"), ("Values", "right"), ("Fetched", "left")], 2,
            "Nothing stored yet: Fetch Ken French, Fetch FRED, or import Shiller's CSV.")
        self._series.setMinimumHeight(300)
        self._fred_note = Note("ph.info", "info")
        self._fred_note.set_text('FRED (the VIX and T-bill history) needs [fred] key = "…" in '
                                 "settings.toml: a free key from fredaccount.stlouisfed.org.")
        self._fred_note.hide()
        card.body.addWidget(self._fred_note)
        self._series.selection_changed.connect(self._preview)
        card.add_action(button("Import Shiller CSV…", icon_name="ph.upload-simple",
                               on_click=lambda: self._act("Import Shiller CSV…")))
        card.add_action(button("Fetch FRED", icon_name="ph.download-simple",
                               on_click=lambda: self._act("Fetch FRED series")))
        card.add_action(button("Fetch Ken French", "primary", "ph.download-simple",
                               on_click=lambda: self._act("Fetch Ken French factors")))
        return card

    def _build_holdings(self) -> Card:
        card, self._holdings = self._table_card(
            "Fund holdings", "Each fund's newest SEC N-PORT report",
            [("Fund", "left"), ("Name", "left"), ("As of", "left"), ("Lines", "right"),
             ("Weight", "right"), ("Reports", "right"), ("Checked", "left")], 1,
            "No fund holdings yet: Fetch holdings… asks for a fund's ticker.")
        self._identity_note = Note("ph.warning", "critical")
        self._identity_note.set_text('Fetching needs [sec] identity = "Your Name you@example.com" '
                                     "in settings.toml (the SEC's fair-access policy).")
        self._identity_note.hide()
        card.body.addWidget(self._identity_note)
        card.add_action(button("Fetch holdings…", icon_name="ph.download-simple",
                               on_click=lambda: self._act("Fetch fund holdings…")))
        return card

    def _build_chains(self) -> Card:
        card, self._chain_table = self._table_card(
            "Option chains", "Snapshots per symbol",
            [("Symbol", "left"), ("Days", "right"), ("Snapshots", "right"),
             ("Latest", "left"), ("Quotes", "right"), ("Source", "left")], 0,
            "No chain snapshots yet.")
        self._watchlist = label("", "muted", wrap=True)
        card.body.addWidget(self._watchlist)
        card.add_action(button("Collect today's chains", icon_name="ph.download-simple",
                               on_click=lambda: self._act("Collect today's chains")))
        return card

    def _build_bars(self) -> Card:
        card, self._bars = self._table_card(
            "Price history", "Daily bars in the store",
            [("Symbol", "left"), ("Source", "left"), ("Basis", "left"), ("Bars", "right"),
             ("From", "left"), ("Through", "left")], 0,
            "No daily bars yet: sync a symbol on Markets, or import a CSV on the Dashboard.")
        card.add_action(button("Refresh", icon_name="ph.arrows-clockwise", on_click=self.refresh))
        return card

    def _build_preview(self) -> Card:
        card = Card("Preview")
        self._preview_stack = QStackedWidget()
        self._preview_stack.addWidget(EmptyState("ph.chart-line", "Select a series",
                                                 "Pick a row in Outside series to plot it here."))
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        self._preview_title = label("", "rowTitle", wrap=True)
        self._preview_chart = StackedTimeSeries()
        self._preview_chart.setMinimumHeight(240)
        self._preview_stats = label("", "muted", wrap=True)
        self._citation = Note("ph.quotes", "info", width=_NOTE_WIDTH)
        for widget in (self._preview_title, self._preview_chart, self._preview_stats,
                       self._citation):
            layout.addWidget(widget)
        layout.addStretch(1)
        self._preview_stack.addWidget(holder)
        card.body.addWidget(self._preview_stack, 1)
        return card

    # ------------------------------------------------------------- reading
    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._tasks.submit(self._read, on_done=self._render, on_error=self._error)

    def _read(self) -> _Overview:
        return _Overview(self._external.catalog(), self._funds.catalog(), self._chains.snapshots(),
                         [s.summary for s in self._workstation.overview().series],
                         self._funds.has_identity, self._collector.watchlist,
                         self._external.has_fred_key)

    def _render(self, o: _Overview) -> None:
        self._series_rows = o.series
        self._fill(self._series, [[r.meta.source, r.meta.series, r.meta.title, r.meta.frequency,
                                r.first, r.last, f"{r.count:,}", _day(r.fetched_at)]
                               for r in o.series])
        self._fill(self._holdings, [[f.fund, f.fund_name, f.report_date, f"{f.lines:,}",
                                  f"{f.weight:.1%}", str(f.reports), _day(f.fetched_at)]
                                 for f in o.funds])
        self._identity_note.setVisible(not o.has_identity)
        self._fred_note.setVisible(not o.has_fred_key)
        per_symbol: dict[str, list] = {}
        for snap in o.chains:                       # newest first
            per_symbol.setdefault(snap.symbol, []).append(snap)
        self._fill(self._chain_table, [[symbol, str(len({s.as_of.date() for s in snaps})),
                                     str(len(snaps)), f"{snaps[0].as_of:%Y-%m-%d %H:%M}",
                                     f"{snaps[0].quotes:,}", snaps[0].source]
                                    for symbol, snaps in sorted(per_symbol.items())])
        self._watchlist.setText(
            f"Collected daily when TWS connects after 09:45 New York: {', '.join(o.watchlist)}."
            if o.watchlist else
            'No daily collection: add [collect] chains = ["SPX", "SPY"] to settings.toml.')
        self._fill(self._bars, [[b.symbol, b.source, b.basis.value, f"{b.n_bars:,}", _day(b.first),
                              _day(b.last)] for b in o.bars])
        self._header.set_status(f"{len(o.series)} outside series · {len(o.funds)} funds · "
                                f"{len(o.chains)} chain snapshots · {len(o.bars)} price histories")

    def _preview(self, row: int) -> None:
        if not 0 <= row < len(self._series_rows):
            return
        meta = self._series_rows[row].meta
        self._tasks.submit(lambda: self._external.series(meta.source, meta.series),
                           on_done=self._plot, on_error=self._error)

    def _plot(self, stored) -> None:
        meta, values = stored.meta, stored.values
        if meta.unit == "fraction":
            shown, fmt = np.cumsum(np.log1p(values.to_numpy())), "{:+.2f}"
            plotted = "Cumulative log return, Σ ln(1 + r)"
        else:
            shown = values.to_numpy()
            fmt = "{:.2f}%" if meta.unit == "percent" else "{:,.2f}"
            plotted = f"Level ({meta.unit})"
        self._preview_chart.set_panels(values.index,
                                       [Panel(plotted, [Series(meta.series, shown, fmt=fmt)])])
        self._preview_title.setText(meta.title)
        note = f" · {meta.note}" if meta.note else ""
        self._preview_stats.setText(f"{len(values):,} {meta.frequency} values, {stored.first:%Y-%m-%d} "
                                    f"to {stored.last:%Y-%m-%d} · fetched "
                                    f"{stored.fetched_at:%Y-%m-%d %H:%M}{note}")
        self._citation.set_text(meta.citation)
        self._preview_stack.setCurrentIndex(1)

    # ------------------------------------------------------------- actions
    def _act(self, command: str) -> None:
        """Run the Ctrl+K command, then queue a refresh behind it."""
        self._shell.run(command)
        self.refresh()

    def _error(self, exc: Exception) -> None:
        self._header.set_status(str(exc), error=True)
