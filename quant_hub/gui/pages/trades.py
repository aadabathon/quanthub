"""Trades: your broker's history, every round trip, checked against your exports (TR5a, D95).

The page on ``services/activity.py``: the transaction history (TR1, TR1b),
its replay against the positions exports (TR2, ``models/ledger.py``), and
its FIFO lots and round trips (TR3, ``models/lots.py``). It measures
nothing about timing yet; that's TR4.

**The side panel:**
- **History:** import a broker's history (Fidelity's Activity & Orders
  download, or the generic layout), one file or several that overlap. What
  each file added is shown, and wording read as ``other`` is named. Every
  import so far is listed.
- **Checked against your exports:** whether the history explains every
  positions export imported on the Portfolio page, the newest import of
  each day. Holdings it doesn't explain, opening gaps, and each interval's
  cash residual (shown, not judged) are listed, plus how often FIFO and
  average cost match the exports' cost basis.
- **Summary:** round trips closed and open, realized P&L on the closed
  ones, winners, longs against shorts, and income kept beside the P&L.

**The main column:**
- **Round trips:** flat to flat for each account and symbol, newest first.
  Selecting one shows its pieces.
- **The selected trip:** each FIFO piece: what opened it and what closed
  it, at what quoted price, the P&L from the cash.
- **Transactions:** the whole history, newest first.

One TaskRunner job loads everything; a few thousand rows take well under a
second.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from quant_hub.data.activity import Transaction
from quant_hub.gui.components import (
    Card,
    CardGrid,
    DataTable,
    Note,
    PageHeader,
    StatTable,
    button,
)
from quant_hub.gui.pages.base import ScrollPage, saved_grid
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L
from quant_hub.models.ledger import Reconciliation
from quant_hub.models.lots import BasisCheck, RoundTrip
from quant_hub.services.activity import (
    ActivityImportError,
    ActivityImportSummary,
    ActivityService,
    FileImported,
)

_NOTE = SIDE_PANEL_WIDTH - 2 * SPACE_L
_ROW = 30
_EMPTY = ("No history yet. Import your broker's activity: Fidelity's Activity & Orders "
          "download, or a CSV in the generic layout (date, account, kind, symbol, quantity, "
          "price, commission, fees, amount).")


@dataclass(frozen=True)
class _Loaded:
    transactions: list[Transaction]
    imports: list[ActivityImportSummary]
    replay: Reconciliation
    trips: tuple[RoundTrip, ...]
    basis: tuple[BasisCheck, ...]


def _money(x: float | None, signed: bool = True) -> str:
    if x is None:
        return "—"
    if not signed:
        return f"${x:,.2f}"
    return f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"


def _ink(x: float | None) -> str:
    if x is None or x == 0:
        return PALETTE.text_secondary
    return PALETTE.good if x > 0 else PALETTE.critical


def _units(x: float) -> str:
    return f"{x:,.0f}" if abs(x - round(x)) < 1e-9 else f"{x:,.4f}".rstrip("0")


class TradesPage(ScrollPage):
    def __init__(self, activity: ActivityService, tasks, state_store) -> None:
        super().__init__()
        self._activity, self._tasks = activity, tasks
        self._trips: list[RoundTrip] = []
        self._import_dir = str(Path.home())
        self._loaded_once = False
        self._import_reported = False     # the note shows an import's result, not a summary
        self._header = PageHeader("Trades", "Your broker's history: every trade and round trip, "
                                            "checked against your exports")
        self.body.addWidget(self._header)
        grid = CardGrid()
        grid.add_column([("trips", self._build_trips(), None),
                         ("pieces", self._build_pieces(), None),
                         ("history", self._build_transactions(), None)], x=0, w=8)
        grid.add_column([("import", self._build_import(), None),
                         ("checked", self._build_checked(), None),
                         ("summary", self._build_summary(), None)], x=8, w=4,
                        min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, "trades.cards")
        self.body.addWidget(grid, 1)

    # ------------------------------------------------------------ building
    def _build_import(self) -> Card:
        card = Card("History", "Your broker's activity, imported; overlapping files are fine")
        self._import_button = button("Import history…", "primary", "ph.upload-simple",
                                     on_click=self._choose_files)
        self._import_button.setToolTip("Fidelity's Activity & Orders download, or the generic "
                                       "layout. Several files at once; overlaps count once.")
        card.body.addWidget(self._import_button)
        self._import_note = Note("ph.info", "info", width=_NOTE)
        self._import_note.set_text(_EMPTY)
        card.body.addWidget(self._import_note)
        self._imports = DataTable([("File", "left"), ("From", "left"), ("To", "left"),
                                   ("Rows", "right"), ("New", "right")], row_height=_ROW)
        self._imports.balance_columns()
        self._imports.setMinimumHeight(120)
        card.body.addWidget(self._imports)
        return card

    def _build_checked(self) -> Card:
        card = Card("Checked against your exports", "The history replayed to every positions "
                                                     "export, the newest import of each day")
        self._checked = StatTable([
            ("The replay", [("span", "History"), ("exports", "Exports replayed"),
                            ("holdings", "Holdings explained"), ("opening", "Opening gaps")]),
            ("Cost basis", [("fifo", "FIFO matches"), ("average", "Average cost matches")]),
        ])
        card.body.addWidget(self._checked)
        self._gaps = Note("ph.warning", "warning", width=_NOTE)
        self._gaps.hide()
        card.body.addWidget(self._gaps)
        self._intervals = DataTable([("From", "left"), ("To", "left"), ("Holdings", "right"),
                                     ("Cash residual", "right")], row_height=_ROW)
        self._intervals.balance_columns()
        self._intervals.setMinimumHeight(120)
        card.body.addWidget(self._intervals)
        cash = Note("ph.info", "info", width=_NOTE)
        cash.set_text("Cash is shown, not judged: pending activity, money-market accrual and "
                      "how a broker splits margin and short cash all move it.")
        card.body.addWidget(cash)
        return card

    def _build_summary(self) -> Card:
        card = Card("Summary", "Realized P&L is from the closed round trips' cash")
        self._summary = StatTable([
            ("Round trips", [("trips", "Closed · open"), ("realized", "Realized"),
                             ("winners", "Winners"), ("long", "Longs"), ("short", "Shorts"),
                             ("income", "Income beside the P&L")]),
        ])
        card.body.addWidget(self._summary)
        return card

    def _build_trips(self) -> Card:
        self._trips_card = Card("Round trips", "Flat to flat, each account and symbol, newest "
                                               "first; select one for its pieces")
        self._trips_table = DataTable([("Symbol", "left"), ("Account", "left"), ("Side", "left"),
                                       ("Opened", "left"), ("Closed", "left"), ("Days", "right"),
                                       ("Invested", "right"), ("Realized", "right"),
                                       ("Return", "right"), ("Income", "right")], row_height=_ROW)
        self._trips_table.balance_columns()
        self._trips_table.setMinimumHeight(360)
        self._trips_table.selection_changed.connect(self._select)
        self._trips_card.body.addWidget(self._trips_table, 1)
        return self._trips_card

    def _build_pieces(self) -> Card:
        self._pieces_card = Card("The selected trip", "Each FIFO piece: what opened it and "
                                                      "what closed it")
        self._pieces = DataTable([("Opened", "left"), ("Closed", "left"), ("Units", "right"),
                                  ("Open fill", "right"), ("Close fill", "right"),
                                  ("Days", "right"), ("P&L", "right")], row_height=_ROW)
        self._pieces.balance_columns()
        self._pieces.setMinimumHeight(160)
        self._pieces_card.body.addWidget(self._pieces, 1)
        return self._pieces_card

    def _build_transactions(self) -> Card:
        self._history_card = Card("Transactions", "The whole history, newest first")
        self._history = DataTable([("Date", "left"), ("Account", "left"), ("Kind", "left"),
                                   ("Symbol", "left"), ("Quantity", "right"), ("Price", "right"),
                                   ("Amount", "right")], row_height=_ROW)
        self._history.balance_columns()
        self._history.setMinimumHeight(320)
        self._history_card.body.addWidget(self._history, 1)
        return self._history_card

    # ------------------------------------------------------------ loading
    def on_show(self) -> None:
        if not self._loaded_once:
            self._loaded_once = True
            self.reload()

    def reload(self) -> None:
        self._header.set_status("Reading the history…")
        self._tasks.submit(self._job, on_done=self._render, on_error=self._error)

    def _job(self) -> _Loaded:
        """Worker thread: the history, its replay, its round trips, the basis."""
        a = self._activity
        return _Loaded(a.transactions(), a.imports(), a.reconcile(), a.round_trips(),
                       a.basis_checks())

    def _error(self, exc: Exception) -> None:
        self._import_button.setEnabled(True)
        self._header.set_status(str(exc), error=True)

    # -------------------------------------------------------------- import
    def _choose_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "Import your broker's activity history: one file or several",
            self._import_dir, "CSV files (*.csv);;All files (*)")
        if paths:
            self._import_dir = str(Path(paths[0]).parent)
            self.import_files([Path(p) for p in paths])

    def import_files(self, paths: list[Path]) -> None:
        """Import history files (all or none), then reload the page."""
        self._import_button.setEnabled(False)
        self._header.set_status(f"Importing {', '.join(p.name for p in paths)}…")
        self._tasks.submit(lambda: self._activity.import_activity(paths),
                           on_done=self._on_imported, on_error=self._on_import_error)

    def _on_imported(self, results: list[FileImported]) -> None:
        self._import_button.setEnabled(True)
        added = sum(r.summary.added for r in results)
        parts = [f"{r.summary.name}: {r.summary.added:,} new of {r.summary.rows:,}"
                 + (" (imported before)" if r.seen_before else "") for r in results]
        unrecognized = [w for r in results for w in r.unrecognized]
        text = "Imported. " + "; ".join(parts) + "."
        if unrecognized:
            text += (f" Kept as other, with no effect on P&L ({len(unrecognized)} wordings): "
                     + "; ".join(unrecognized[:5]) + ("…" if len(unrecognized) > 5 else ""))
        self._import_note.set_text(text)
        self._import_reported = True
        self._header.set_status(f"Imported {added:,} new transactions.")
        self.reload()

    def _on_import_error(self, exc: Exception) -> None:
        self._import_button.setEnabled(True)
        if isinstance(exc, ActivityImportError):
            shown = exc.problems[:8]
            more = len(exc.problems) - len(shown)
            self._import_note.set_text("Nothing was imported. " + " ".join(shown)
                                       + (f" … and {more} more." if more else ""))
            self._import_reported = True
            self._header.set_status(f"Nothing was imported: {len(exc.problems)} problems.",
                                    error=True)
            return
        self._error(exc)

    # ---------------------------------------------------------- rendering
    def _render(self, loaded: _Loaded) -> None:
        self._import_button.setEnabled(True)
        history = loaded.transactions
        if not history:
            self._import_note.set_text(_EMPTY)
        elif not self._import_reported:
            self._import_note.set_text(f"{len(history):,} transactions from "
                                       f"{len(loaded.imports)} import"
                                       f"{'s' * (len(loaded.imports) != 1)}. Import more at any "
                                       "time: what's already stored is counted once.")
        self._imports.set_rows([[i.name, i.first_date.isoformat(), i.last_date.isoformat(),
                                 f"{i.rows:,}", f"{i.added:,}"] for i in loaded.imports])
        self._render_checked(loaded)
        self._render_summary(loaded.trips)
        self._trips = sorted(loaded.trips, key=lambda t: (t.opened, t.symbol), reverse=True)
        self._trips_table.set_rows([self._trip_row(t) for t in self._trips])
        self._history.set_rows([[t.trade_date.isoformat(), t.account, t.kind.value, t.symbol,
                                 _units(t.quantity) if t.quantity else "",
                                 f"{t.price:,.2f}" if t.price is not None else "",
                                 (_money(t.amount), _ink(t.amount))]
                                for t in reversed(history)])
        self._history_card.set_subtitle(f"{len(history):,} transactions, newest first")
        self._pieces.set_rows([])
        if history:
            self._header.set_status(
                f"{len(history):,} transactions, {len(loaded.trips)} round trips. "
                + ("Every export is explained." if loaded.replay.reconciled
                   else "Some holdings aren't explained: see Checked against your exports."))
        else:
            self._header.set_status("No history yet: import your broker's activity.")
        if self._trips:      # the newest trip with something closed, else the newest
            first = next((i for i, trip in enumerate(self._trips) if trip.pieces), 0)
            self._trips_table.selectRow(first)

    def _render_checked(self, loaded: _Loaded) -> None:
        replay, checked = loaded.replay, self._checked
        holdings = [h for i in replay.intervals for h in i.holdings]
        explained = sum(h.reconciled for h in holdings)
        checked.set_value("span", f"{replay.first_day} to {replay.last_day}"
                          if replay.first_day else "—")
        checked.set_value("exports", f"{replay.snapshots}" if replay.snapshots
                          else "none imported on Portfolio")
        checked.set_value("holdings", f"{explained} of {len(holdings)}" if holdings else "—")
        checked.set_value("opening", f"{len(replay.opening)}")
        basis = loaded.basis
        checked.set_value("fifo", f"{sum(b.fifo_matches for b in basis)} of {len(basis)}"
                          if basis else "—")
        checked.set_value("average", f"{sum(b.average_matches for b in basis)} of {len(basis)}"
                          if basis else "—")
        lines = [f"At the first export ({replay.first_snapshot}): {g.symbol} held "
                 f"{_units(g.after)}, the history reaches {_units(g.traded)}"
                 for g in replay.opening]
        for interval in replay.intervals:
            for g in interval.gaps:
                lines.append(f"{interval.start.date()} → {interval.end.date()}: {g.symbol} "
                             f"{_units(g.before)} → {_units(g.after)}, traded "
                             f"{_units(g.traded)}, unexplained {g.residual:+,.4g}")
        self._gaps.set_text(" · ".join(lines[:12]) + (" …" if len(lines) > 12 else ""))
        self._gaps.setVisible(bool(lines))
        self._intervals.set_rows([[i.start.date().isoformat(), i.end.date().isoformat(),
                                   (f"{len(i.holdings) - len(i.gaps)}/{len(i.holdings)}",
                                    PALETTE.good if i.reconciled else PALETTE.critical),
                                   _money(i.cash_residual)] for i in replay.intervals])

    def _render_summary(self, trips: tuple[RoundTrip, ...]) -> None:
        closed = [t for t in trips if t.closed is not None]
        known = [t for t in closed if t.realized is not None]
        s = self._summary
        s.set_value("trips", f"{len(closed)} · {len(trips) - len(closed)}")
        total = sum(t.realized for t in known) if known else None
        s.set_value("realized", _money(total))
        wins = sum(t.realized > 0 for t in known)
        s.set_value("winners", f"{wins} of {len(known)}" if known else "—")
        for side in ("long", "short"):
            group = [t for t in known if t.side == side]
            s.set_value(side, f"{len(group)}, {_money(sum(t.realized for t in group))}"
                        if group else "—")
        s.set_value("income", _money(sum(t.income for t in trips)) if trips else "—")

    def _trip_row(self, t: RoundTrip) -> list:
        end = t.closed or date.today()
        realized = t.realized if t.pieces else None
        ret = t.return_on_invested
        return [t.symbol, t.account.split(" - ")[0], t.side, t.opened.isoformat(),
                t.closed.isoformat() if t.closed else ("open", PALETTE.series),
                f"{(end - t.opened).days:,}",
                _money(t.invested, signed=False), (_money(realized), _ink(realized)),
                (f"{ret:+.1%}" if ret is not None else "—", _ink(ret)),
                _money(t.income) if t.income else ""]

    def _select(self, row: int) -> None:
        if not 0 <= row < len(self._trips):
            self._pieces.set_rows([])
            return
        trip = self._trips[row]
        pieces = (f"{len(trip.pieces)} piece{'s' * (len(trip.pieces) != 1)} closed"
                  if trip.pieces else "nothing closed yet")
        held = f"; {_units(trip.open_units)} still held" if trip.closed is None else ""
        self._pieces_card.set_subtitle(f"{trip.symbol}, {trip.side}, {trip.opened} to "
                                       f"{trip.closed or 'open'}: {pieces}{held}")
        self._pieces.set_rows([[p.opened.isoformat(), p.closed.isoformat(), _units(p.units),
                                f"{p.open_fill:,.2f}" if p.open_fill is not None else "—",
                                f"{p.close_fill:,.2f}" if p.close_fill is not None else "—",
                                f"{p.days_held:,}", (_money(p.pnl), _ink(p.pnl))]
                               for p in trip.pieces])
