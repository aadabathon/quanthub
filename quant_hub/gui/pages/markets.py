"""Markets: watchlist quotes with explicit source and provenance (D22/D23).

The page owns its data source; nothing elsewhere changes what these rows
mean. Each row shows Last / Mode / As of / Source, where Mode is the quote's
``MarketDataMode`` as a badge (state color + text). A quote that couldn't be
obtained is an explicit UNAVAILABLE row, and the inspector on the right says
why — the log is never the only place the explanation lives.

The inspector also shows what the local store holds for the selected
symbol (via ``services.workstation``) and offers "Sync 1Y history", which
fetches through the page's source; for ``ibkr`` that persists via the
SQLite cache (D17–D21).

Threading: a refresh is one TaskRunner job returning per-symbol outcomes, so
one unentitled instrument cannot sink the batch (as in the Tk page).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QStackedWidget, QVBoxLayout, QWidget

from quant_hub.data.sources import SourceOfflineError
from quant_hub.data.types import MarketDataMode, PriceBasis, Quote
from quant_hub.tasks import TaskRunner
from quant_hub.gui.charts import Sparkline
from quant_hub.gui.components import (
    CardGrid,
    Badge,
    Card,
    DataTable,
    EmptyState,
    Note,
    PageHeader,
    Select,
    button,
    centered,
    divider,
    label,
)
from quant_hub.gui.pages.base import ScrollPage, Shell, saved_grid
from quant_hub.gui.theme import (
    CONTROL_HEIGHT,
    FIELD_M,
    PALETTE,
    SIDE_PANEL_WIDTH,
    SPACE_L,
    SPACE_M,
    SPACE_S,
)
from quant_hub.services.market_service import MarketService
from quant_hub.services.workstation import SeriesSnapshot, WorkstationOverview, WorkstationService
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

_STATE_KEY = "markets"             # shared with the Tk page
_DEFAULT_WATCHLIST = ("SPY", "QQQ", "AAPL", "MSFT", "TLT", "GLD")
_SYNC_DAYS = 365
_SPARK_WIDTH = 116

# Badge tone per provenance: live is good, delayed is a caution, frozen and
# synthetic are informational. Tones are state colors and the text always
# names the state, so color is never the only signal.
_MODE_TONE = {
    MarketDataMode.LIVE: "good",
    MarketDataMode.DELAYED: "warning",
    MarketDataMode.DELAYED_FROZEN: "warning",
    MarketDataMode.FROZEN: "info",
    MarketDataMode.SYNTHETIC: "info",
    MarketDataMode.UNKNOWN: "neutral",
}

Outcome = Quote | Exception | None   # None = not fetched yet


def _fmt_time(ts: datetime, full: bool = False) -> str:
    """Time of day for today's quotes, else the date (the table) or the date
    and time (``full``, the inspector)."""
    if ts.date() == datetime.now().date():
        return f"{ts:%H:%M:%S}"
    return f"{ts:%Y-%m-%d %H:%M}" if full else f"{ts:%Y-%m-%d}"


def _badge_for(outcome: Outcome) -> tuple[str, str]:
    if outcome is None:
        return "PENDING", "neutral"
    if isinstance(outcome, Exception):
        return "UNAVAILABLE", "critical"
    return outcome.mode.value.upper(), _MODE_TONE[outcome.mode]


class MarketsPage(ScrollPage):
    def __init__(self, shell: Shell, market_service: MarketService,
                 workstation: WorkstationService, tasks: TaskRunner,
                 state_store: ViewStateStore) -> None:
        super().__init__()
        self._shell = shell
        self._market = market_service
        self._workstation = workstation
        self._tasks = tasks
        self._state_store = state_store
        saved = state_store.load(_STATE_KEY)
        self._symbols: list[str] = list(saved.get("symbols") or _DEFAULT_WATCHLIST)
        self._outcomes: dict[str, Outcome] = {s: None for s in self._symbols}
        self._stored: dict[str, SeriesSnapshot] = {}
        self._selected: str | None = None
        self._refreshed_once = False
        names = market_service.source_names
        source = saved.get("source")
        self._source_name = source if source in names else ("ibkr" if "ibkr" in names else names[0])

        self._header = PageHeader(
            "Markets", "Watchlist quotes with source, freshness and provenance")
        self.body.addWidget(self._header)
        grid = CardGrid()
        grid.add("watchlist", self._build_watchlist(names), 0, 0, 9, 22)
        grid.add_column([("details", self._build_inspector(), 22)], x=9, w=3,
                        min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, f"{_STATE_KEY}.cards")
        self.body.addWidget(grid, 1)
        self._render_table()

    # ------------------------------------------------------------ building
    def _build_watchlist(self, names: list[str]) -> Card:
        self._card = Card("Watchlist", "")
        toolbar = QHBoxLayout()
        toolbar.setSpacing(SPACE_S)
        self._source = Select(min_width=FIELD_M)
        self._source.setFixedWidth(FIELD_M)
        self._source.set_items(names, self._source_name)
        self._source.setToolTip("Data source for every row on this page")
        self._source.current_changed.connect(self._on_source)
        toolbar.addWidget(label("Source", "fieldCaption"))
        toolbar.addWidget(self._source)
        toolbar.addSpacing(SPACE_L)
        self._add_edit = QLineEdit()
        self._add_edit.setPlaceholderText("Add symbol…")
        self._add_edit.setFixedSize(FIELD_M, CONTROL_HEIGHT)
        self._add_edit.returnPressed.connect(self._add_symbol)
        self._add_edit.textEdited.connect(
            lambda t: self._add_edit.setText(t.upper()) if t != t.upper() else None)
        toolbar.addWidget(self._add_edit)
        toolbar.addWidget(button("Add", icon_name="ph.plus", on_click=self._add_symbol))
        toolbar.addStretch(1)
        self._refresh_button = button("Refresh", "primary", "ph.arrows-clockwise",
                                      on_click=self._refresh)
        toolbar.addWidget(self._refresh_button)
        self._card.body.addLayout(toolbar)

        self._table = DataTable(
            [("Symbol", "left"), ("Last", "right"), ("Mode", "left"), ("As of", "right"),
             ("Source", "right"), ("Stored 1Y", "left")],
            row_height=48, focusable=True)
        self._table.fix_column(2, 132)                         # badge
        self._table.fix_column(5, _SPARK_WIDTH + 2 * SPACE_S)  # sparkline
        self._table.balance_columns()
        self._table.selection_changed.connect(self._on_row_selected)
        delete = QShortcut(QKeySequence.Delete, self._table, context=Qt.WidgetShortcut)
        delete.activated.connect(self._remove_selected)
        self._table.setMinimumHeight(360)
        self._card.body.addWidget(self._table, 1)
        hint = label("Select a row for details · Delete removes it", "muted")
        self._card.body.addWidget(hint)
        return self._card

    def _build_inspector(self) -> QWidget:
        card = Card("Details")
        self._detail_stack = QStackedWidget()
        self._detail_empty = EmptyState("ph.hand-pointing", "Select a symbol",
                                        "Pick a row to see its quote, provenance and stored "
                                        "history.")
        self._detail_stack.addWidget(self._detail_empty)

        detail = QWidget()
        col = QVBoxLayout(detail)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_M)
        top = QHBoxLayout()
        self._d_symbol = label("", "heroSymbol")
        top.addWidget(self._d_symbol)
        top.addStretch(1)
        self._d_badge = Badge()
        top.addWidget(self._d_badge, 0, Qt.AlignVCenter)
        col.addLayout(top)
        self._d_price = label("—", "heroValue")
        col.addWidget(self._d_price)
        self._d_meta = label("", "muted", wrap=True)
        col.addWidget(self._d_meta)
        self._d_note = Note()
        col.addWidget(self._d_note)
        col.addWidget(divider())
        col.addWidget(label("STORED HISTORY", "groupTitle"))
        self._spark_holder = QVBoxLayout()
        self._spark_holder.setContentsMargins(0, 0, 0, 0)
        col.addLayout(self._spark_holder)
        self._d_stored = label("", "muted", wrap=True)
        col.addWidget(self._d_stored)
        col.addSpacing(SPACE_S)
        self._sync_button = button("Sync 1Y history", icon_name="ph.download-simple",
                                   on_click=self._sync_history)
        self._chart_button = button("Open in market explorer", icon_name="ph.chart-line-up",
                                    on_click=self._open_in_explorer)
        self._remove_button = button("Remove from watchlist", "ghost", "ph.trash",
                                     on_click=self._remove_selected)
        for widget in (self._sync_button, self._chart_button, self._remove_button):
            col.addWidget(widget)
        col.addStretch(1)
        self._detail_stack.addWidget(detail)
        self._detail_widget = detail
        card.body.addWidget(self._detail_stack, 1)
        return card

    # ----------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        self._load_stored()
        if not self._refreshed_once:
            self._refresh()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {"symbols": self._symbols,
                                            "source": self._source_name})

    # -------------------------------------------------------------- quotes
    def _refresh(self, symbols: Sequence[str] | None = None) -> None:
        self._refreshed_once = True
        symbols = list(symbols or self._symbols)
        if not symbols:
            return
        source = self._source_name
        self._refresh_button.setEnabled(False)
        self._header.set_status(f"Refreshing {len(symbols)} quote(s) from {source}…")

        def fetch() -> list[tuple[str, Quote | Exception]]:
            results: list[tuple[str, Quote | Exception]] = []
            for symbol in symbols:
                try:
                    results.append((symbol, self._market.get_quote(symbol, source=source)))
                except Exception as exc:   # per-symbol: one failure can't sink the batch
                    results.append((symbol, exc))
            return results

        self._tasks.submit(fetch, on_done=lambda r: self._on_quotes(source, r),
                           on_error=self._on_refresh_failed)

    def _on_quotes(self, source: str, results: list[tuple[str, Quote | Exception]]) -> None:
        self._refresh_button.setEnabled(True)
        if source != self._source_name:
            return   # the source changed while this batch ran; its refresh follows
        for symbol, outcome in results:
            if symbol not in self._outcomes:
                continue   # removed while the fetch ran
            self._outcomes[symbol] = outcome
            if isinstance(outcome, Exception):
                _logger.warning("quote unavailable for %s: %s", symbol, outcome)
        failed = sum(isinstance(o, Exception) for _, o in results)
        self._header.set_status(
            f"{failed} of {len(results)} unavailable from {source} — select a row for the reason"
            if failed else f"Updated {len(results)} quote(s) from {source} at {datetime.now():%H:%M:%S}")
        self._render_table()

    def _on_refresh_failed(self, exc: Exception) -> None:
        self._refresh_button.setEnabled(True)
        self._header.set_status(f"Refresh failed: {exc}", error=True)
        _logger.error("watchlist refresh failed: %s", exc)

    def _on_source(self, source: str) -> None:
        self._source_name = source
        self._outcomes = {s: None for s in self._symbols}
        self._render_table()
        self._refresh()

    # ----------------------------------------------------------- watchlist
    def _add_symbol(self) -> None:
        symbol = self._add_edit.text().strip().upper()
        self._add_edit.clear()
        if not symbol:
            return
        if symbol in self._symbols:
            self._select(symbol)
            return
        self._symbols.append(symbol)
        self._outcomes[symbol] = None
        self._render_table()
        self._select(symbol)
        self._refresh([symbol])

    def _remove_selected(self) -> None:
        if self._selected is None:
            return
        symbol = self._selected
        self._symbols.remove(symbol)
        self._outcomes.pop(symbol, None)
        self._selected = None
        self._render_table()
        self._header.set_status(f"Removed {symbol} from the watchlist")

    def _render_table(self) -> None:
        rows = []
        for symbol in self._symbols:
            outcome = self._outcomes.get(symbol)
            text, tone = _badge_for(outcome)
            badge = centered(Badge(text, tone))
            stored = self._stored.get(symbol)
            spark = (centered(Sparkline(stored.spark, width=_SPARK_WIDTH))
                     if stored is not None and len(stored.spark) >= 2 else ("—", PALETTE.muted))
            if isinstance(outcome, Quote):
                rows.append([symbol, f"{outcome.price:,.2f}", badge,
                             _fmt_time(outcome.timestamp), outcome.source.upper(), spark])
            else:
                rows.append([symbol, "—", badge, "—", self._source_name.upper(), spark])
        self._table.blockSignals(True)
        self._table.set_rows(rows)
        self._table.clearSelection()
        if self._selected in self._symbols:
            self._table.selectRow(self._symbols.index(self._selected))
        self._table.blockSignals(False)
        unavailable = sum(isinstance(o, Exception) for o in self._outcomes.values())
        modes = [o.mode.value for o in self._outcomes.values() if isinstance(o, Quote)]
        parts = [f"{len(self._symbols)} symbols", f"source {self._source_name}"]
        for mode in sorted(set(modes)):
            parts.append(f"{modes.count(mode)} {mode}")
        if unavailable:
            parts.append(f"{unavailable} unavailable")
        self._card.set_subtitle(" · ".join(parts))
        self._render_detail()

    def _select(self, symbol: str) -> None:
        self._table.selectRow(self._symbols.index(symbol))

    def _on_row_selected(self, row: int) -> None:
        self._selected = self._symbols[row] if 0 <= row < len(self._symbols) else None
        self._render_detail()

    # ------------------------------------------------------------- inspector
    def _render_detail(self) -> None:
        symbol = self._selected
        if symbol is None:
            self._detail_stack.setCurrentWidget(self._detail_empty)
            return
        self._detail_stack.setCurrentWidget(self._detail_widget)
        outcome = self._outcomes.get(symbol)
        self._d_symbol.setText(symbol)
        self._d_badge.set_badge(*_badge_for(outcome))
        if isinstance(outcome, Quote):
            self._d_price.setText(f"{outcome.price:,.2f}")
            self._d_meta.setText(f"As of {_fmt_time(outcome.timestamp, full=True)} · "
                                 f"{outcome.source.upper()} · {outcome.mode.value}")
        else:
            self._d_price.setText("—")
            self._d_meta.setText("Not fetched yet" if outcome is None
                                 else f"No quote from {self._source_name}")
        self._d_note.setVisible(isinstance(outcome, Exception))
        if isinstance(outcome, Exception):
            self._d_note.set_text(str(outcome))

        while self._spark_holder.count():
            old = self._spark_holder.takeAt(0).widget()
            old.hide()            # deleteLater runs on the next loop pass
            old.deleteLater()
        stored = self._stored.get(symbol)
        if stored is not None and len(stored.spark) >= 2:
            self._spark_holder.addWidget(Sparkline(stored.spark, width=SIDE_PANEL_WIDTH - 2 * SPACE_L,
                                                   height=84))
            s = stored.summary
            bars = "daily bars" if s.bar_size == "1 day" else f"bars ({s.bar_size})"
            self._d_stored.setText(f"{s.n_bars:,} {bars} · {s.first:%Y-%m-%d} → "
                                   f"{s.last:%Y-%m-%d} · {s.source.upper()}\n"
                                   "The sparkline shows the trailing year.")
        else:
            self._d_stored.setText(f"Nothing stored for {symbol} yet. Sync to fetch a year of "
                                   "daily bars into the local store.")
        self._chart_button.setVisible(stored is not None)

    def _load_stored(self) -> None:
        self._tasks.submit(self._workstation.overview, on_done=self._on_stored,
                           on_error=lambda exc: _logger.error("store overview failed: %s", exc))

    def _on_stored(self, overview: WorkstationOverview) -> None:
        self._stored = {}
        for snapshot in overview.series:
            self._stored.setdefault(snapshot.symbol, snapshot)
        self._render_table()

    # --------------------------------------------------------------- actions
    def _sync_history(self) -> None:
        symbol, source = self._selected, self._source_name
        if symbol is None:
            return
        end = datetime.now()
        start = end - timedelta(days=_SYNC_DAYS)
        self._sync_button.setEnabled(False)
        self._header.set_status(f"Syncing {symbol} daily history from {source}…")

        def work() -> str:
            history = self._market.get_history(symbol, start, end, source=source,
                                               basis=PriceBasis.TRADED)
            if history.offline_reason:      # served from the store: nothing was synced
                raise SourceOfflineError(
                    f"{source} is offline ({history.offline_reason}); the store holds "
                    f"{len(history):,} bars through {history.end:%Y-%m-%d}")
            return (f"{symbol}: {len(history):,} bars available "
                    f"{history.start:%Y-%m-%d} → {history.end:%Y-%m-%d} ({source})")

        def done(text: str) -> None:
            self._sync_button.setEnabled(True)
            self._header.set_status(text)
            self._load_stored()

        def failed(exc: Exception) -> None:
            self._sync_button.setEnabled(True)
            self._header.set_status(f"Sync failed: {exc}", error=True)
            _logger.error("history sync failed for %s: %s", symbol, exc)

        self._tasks.submit(work, on_done=done, on_error=failed)

    def _open_in_explorer(self) -> None:
        if self._selected is not None:
            self._shell.navigate("Dashboard").select_symbol(self._selected)
