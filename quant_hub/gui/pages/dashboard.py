"""Dashboard: the workstation overview, rebuilt around the local store.

Everything on this page is read from the SQLite store through
``services.workstation`` — never the network — so it is identical with IBKR
connected or not, and it says so ("stored daily closes · not live").

Layout: KPI tiles; Market explorer (price + volume of one stored series over
a window) beside a watchlist of every stored series; then a model sandbox
(GBM fitted to the explorer's symbol, fanned forward), the store table, and
shortcuts to the research pages.

Threading: three kinds of TaskRunner job — the overview, a windowed history,
and a forecast. The worker is FIFO, but the user can change symbol faster
than jobs finish, so each series request carries a token and results for a
superseded token are dropped rather than painted over newer ones.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QStackedWidget,
)

from quant_hub.config import AppConfig
from quant_hub.data.types import PriceHistory
from quant_hub.tasks import TaskRunner
from quant_hub.gui.charts import FanChart, PriceVolumeChart, Sparkline
from quant_hub.gui.components import (
    Card,
    DataTable,
    ElidedLabel,
    EmptyState,
    KpiTile,
    MenuButton,
    NavRow,
    PageHeader,
    SegmentedControl,
    Select,
    button,
    label,
)
from quant_hub.gui.pages.base import ScrollPage, Shell
from quant_hub.gui.theme import PALETTE, SPACE_L, SPACE_M
from quant_hub.services.file_import import (
    FILE_SOURCE,
    FileImportService,
    ImportedSeries,
    symbol_from_filename,
)
from quant_hub.services.workstation import (
    WINDOWS,
    GBMForecast,
    SeriesSnapshot,
    WorkstationOverview,
    WorkstationService,
    gbm_forecast,
)
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

_STATE_KEY = "qt_dashboard"
_FORECAST_WINDOW = "1Y"
_NARROW_PX = 1180   # below this content width the bottom row reflows to 2 + 1

_TOOLS = (
    ("ph.chart-line", "Volatility analysis", "SVI smiles, term structure, GARCH overlay",
     "Volatility Lab"),
    ("ph.trend-up", "Options pricing", "Black–Scholes, greeks, multi-leg strategies",
     "Options Lab"),
    ("ph.chart-pie-slice", "Portfolio risk", "Your holdings: VaR, drawdown, beta, sizing",
     "Portfolio"),
    ("ph.file-text", "Custom research", "Experiments, backtests & paper library",
     "Research"),
)


def _empty_text() -> tuple[str, str]:
    return ("No stored history yet",
            "Import a CSV of daily bars from the Market data card's menu, or connect "
            "IBKR and use “Sync 1Y history” on the Markets page.")


class DashboardPage(ScrollPage):
    def __init__(self, shell: Shell, workstation: WorkstationService,
                 file_import: FileImportService, tasks: TaskRunner,
                 state_store: ViewStateStore, config: AppConfig) -> None:
        super().__init__()
        self._shell = shell
        self._workstation = workstation
        self._file_import = file_import
        self._tasks = tasks
        self._state_store = state_store
        self._config = config
        saved = state_store.load(_STATE_KEY)
        self._symbol: str | None = saved.get("symbol") or config.default_symbol
        self._window: str = saved.get("window") if saved.get("window") in WINDOWS else "1Y"
        self._import_dir: str = saved.get("import_dir") or str(Path.home())
        self._overview: WorkstationOverview | None = None
        # Series by explorer key: the symbol, or "SPY · file" when the store
        # holds it from more than one source (IBKR and an imported file).
        self._series_by_key: dict[str, SeriesSnapshot] = {}
        self._pick: tuple[str, str] | None = None   # (symbol, source) to chart next
        self._loaded_key: tuple | None = None   # (symbol, n_bars, last) currently charted
        self._token = 0

        self._header = PageHeader("Workstation overview",
                                  "Your markets. Your models. Your edge.")
        self.body.addWidget(self._header)
        self.body.addLayout(self._build_kpis())

        top = QHBoxLayout()
        top.setSpacing(SPACE_L)
        top.addWidget(self._build_explorer(), 5)
        top.addWidget(self._build_watchlist(), 2)
        # Rows share any spare height 3:2, so the page fills a tall window
        # and scrolls (rather than squeezes) on a short one.
        self.body.addLayout(top, 3)

        self._bottom = QGridLayout()
        self._bottom.setHorizontalSpacing(SPACE_L)
        self._bottom.setVerticalSpacing(SPACE_L)
        self._sandbox = self._build_sandbox()
        self._store_card = self._build_store_table()
        self._tools = self._build_tools()
        self._narrow: bool | None = None
        self.body.addLayout(self._bottom, 2)
        self._reflow(narrow=False)

    # ------------------------------------------------------------- building
    def _build_kpis(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACE_L)
        self._kpi_series = KpiTile("ph.chart-bar", "Tracked instruments")
        self._kpi_bars = KpiTile("ph.database", "Stored market bars")
        self._kpi_synced = KpiTile("ph.calendar-blank", "Last data sync")
        self._kpi_store = KpiTile("ph.folder", "Data store")
        self._kpi_store.set_value("Local", str(self._config.db_path))
        for tile in (self._kpi_series, self._kpi_bars, self._kpi_synced, self._kpi_store):
            row.addWidget(tile, 1)
        return row

    def _build_explorer(self) -> Card:
        card = Card("Market explorer", "Stored daily history · hover the chart for values")
        controls = QHBoxLayout()
        controls.setSpacing(SPACE_M)
        self._symbol_box = Select()
        self._symbol_box.current_changed.connect(self._on_symbol_chosen)
        controls.addWidget(self._symbol_box)
        # Elided, so the meta line never sets the page's minimum width. It
        # takes the spare width that a stretch used to.
        self._series_meta = ElidedLabel("", "muted")
        controls.addWidget(self._series_meta, 1)
        self._windows = SegmentedControl(list(WINDOWS), self._window)
        self._windows.changed.connect(self._on_window_chosen)
        controls.addWidget(self._windows)
        card.body.addLayout(controls)

        self._explorer_stack = QStackedWidget()
        self._price_chart = PriceVolumeChart()
        self._explorer_empty = EmptyState("ph.chart-line-up", *_empty_text())
        self._explorer_stack.addWidget(self._price_chart)
        self._explorer_stack.addWidget(self._explorer_empty)
        self._explorer_stack.setCurrentWidget(self._explorer_empty)
        self._explorer_stack.setMinimumHeight(260)
        card.body.addWidget(self._explorer_stack, 1)
        return card

    def _build_watchlist(self) -> Card:
        card = Card("Watchlist", "Stored daily closes · not live")
        card.add_action(MenuButton([("Refresh", self.refresh)]))
        self._watchlist = DataTable(
            [("Symbol", "left"), ("Price", "right"), ("1Y", "left"), ("Δ 1D", "right")],
            stretch=2, row_height=48,
        )
        self._watchlist.row_activated.connect(self._on_watch_row)
        card.body.addWidget(self._watchlist, 1)
        card.setMinimumWidth(360)
        return card

    def _build_sandbox(self) -> Card:
        card = Card("Model sandbox", "GBM fit to one year of the explorer's symbol")
        card.add_action(button("Launch simulator", "primary", "ph.arrow-right",
                               on_click=lambda: self._shell.navigate("Simulator")))
        self._sandbox_stack = QStackedWidget()
        self._fan = FanChart()
        self._fan_empty = EmptyState("ph.dice-five", "No forecast yet",
                                     "Pick a stored symbol to fit and simulate.")
        self._sandbox_stack.addWidget(self._fan)
        self._sandbox_stack.addWidget(self._fan_empty)
        self._sandbox_stack.setCurrentWidget(self._fan_empty)
        self._sandbox_stack.setMinimumHeight(200)
        card.body.addWidget(self._sandbox_stack, 1)
        return card

    def _build_store_table(self) -> Card:
        card = Card("Market data", f"Local store: {self._config.db_path}")
        card.add_action(MenuButton([("Import CSV…", self._choose_files),
                                    ("Refresh", self.refresh),
                                    ("Go to Markets", lambda: self._shell.navigate("Markets"))]))
        self._store_table = DataTable(
            [("Symbol", "left"), ("Interval", "left"), ("Bars", "right"),
             ("Date range", "left"), ("Source", "right")],
            stretch=3,
        )
        card.body.addWidget(self._store_table, 1)
        return card

    def _build_tools(self) -> Card:
        card = Card("Research tools", "Quantitative research and analysis")
        for icon_name, title, subtitle, page in _TOOLS:
            row = NavRow(icon_name, title, subtitle)
            row.clicked.connect(lambda p=page: self._shell.navigate(p))
            card.body.addWidget(row)
        card.body.addStretch(1)
        return card

    def _reflow(self, narrow: bool) -> None:
        """Three columns when wide; sandbox + store over full-width tools
        when narrow, so the store table never truncates its date ranges."""
        if narrow == self._narrow:
            return
        self._narrow = narrow
        for widget in (self._sandbox, self._store_card, self._tools):
            self._bottom.removeWidget(widget)
        self._bottom.addWidget(self._sandbox, 0, 0)
        self._bottom.addWidget(self._store_card, 0, 1)
        if narrow:
            self._bottom.addWidget(self._tools, 1, 0, 1, 2)
            stretches = (1, 1, 0)
        else:
            self._bottom.addWidget(self._tools, 0, 2)
            stretches = (5, 5, 3)
        for col, stretch in enumerate(stretches):
            self._bottom.setColumnStretch(col, stretch)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._reflow(narrow=self.width() < _NARROW_PX)

    # ------------------------------------------------------------ lifecycle
    def on_show(self) -> None:
        self.refresh()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {"symbol": self._symbol, "window": self._window,
                                            "import_dir": self._import_dir})

    # -------------------------------------------------------------- import
    def _choose_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "Import daily bars from CSV", self._import_dir,
            "CSV files (*.csv);;All files (*)")
        if paths:
            self._import_dir = str(Path(paths[0]).parent)
            self.import_files([Path(p) for p in paths])

    def import_files(self, paths: list[Path]) -> None:
        """Import each file under its symbol: the file name when it is one
        (``SPY.csv``), else asked for. A file with a symbol column names its
        own, whatever is passed (services/file_import.py)."""
        jobs = []
        for path in paths:
            symbol = symbol_from_filename(path)
            if symbol is None:
                symbol, ok = QInputDialog.getText(
                    self, "Import CSV", f"Which symbol are the bars in {path.name}?")
                if not ok or not symbol.strip():
                    continue
            jobs.append((path, symbol))
        if not jobs:
            return
        self._header.set_status(f"Importing {len(jobs)} file{'s' if len(jobs) != 1 else ''}…")

        def work() -> tuple[list[ImportedSeries], list[str]]:
            imported, failed = [], []
            for path, symbol in jobs:
                try:
                    imported += self._file_import.import_bars(path, symbol)
                except Exception as exc:   # per file: one bad file can't sink the rest
                    _logger.error("import of %s failed: %s", path.name, exc)
                    failed.append(f"{path.name}: {exc}")
            return imported, failed

        self._tasks.submit(work, on_done=self._on_imported, on_error=self._on_import_error)

    def _on_imported(self, result: tuple[list[ImportedSeries], list[str]]) -> None:
        imported, failed = result
        parts = [f"Imported {s.describe()}" for s in imported] + failed
        self._header.set_status(" · ".join(parts) or "Nothing imported", error=bool(failed))
        if imported:
            self._pick = (imported[0].symbol, FILE_SOURCE)   # chart what was just imported
            self.refresh()

    def _on_import_error(self, exc: Exception) -> None:
        self._header.set_status(f"Import failed: {exc}", error=True)

    def refresh(self) -> None:
        self._tasks.submit(self._workstation.overview, on_done=self._render_overview,
                           on_error=self._overview_failed)

    def select_symbol(self, symbol: str) -> None:
        """Public: chart ``symbol``, an explorer key or a plain symbol (used by
        the Ctrl+K search)."""
        key = self._key_for(symbol)
        if key is not None:
            self._symbol_box.set_current(key)

    def _key_for(self, wanted: str | None) -> str | None:
        if wanted in self._series_by_key:
            return wanted
        return next((key for key, s in self._series_by_key.items() if s.symbol == wanted), None)

    # -------------------------------------------------------------- render
    def _render_overview(self, overview: WorkstationOverview) -> None:
        self._overview = overview
        series = overview.series
        self._kpi_series.set_value(str(len(series)))
        self._kpi_bars.set_value(f"{overview.total_bars:,}")
        synced = overview.last_synced
        self._kpi_synced.set_value(f"{synced:%b %d, %Y}" if synced else "—")

        counts = Counter(s.symbol for s in series)
        self._series_by_key = {
            (s.symbol if counts[s.symbol] == 1 else f"{s.symbol} · {s.summary.source}"): s
            for s in series}
        self._watchlist.set_rows([self._watch_row(key, s)
                                  for key, s in self._series_by_key.items()])
        self._store_table.set_rows([
            [s.symbol, s.summary.bar_size, f"{s.summary.n_bars:,}",
             f"{s.summary.first:%Y-%m-%d} → {s.summary.last:%Y-%m-%d}",
             s.summary.source.upper()]
            for s in series
        ])
        self._shell.publish_commands("symbols", {
            f"Chart {key}  ·  stored {s.summary.bar_size}": (
                lambda k=key: self._shell.navigate("Dashboard").select_symbol(k))
            for key, s in self._series_by_key.items()
        })

        keys = list(self._series_by_key)
        wanted = None
        if self._pick is not None:
            wanted = next((key for key, s in self._series_by_key.items()
                           if (s.symbol, s.summary.source) == self._pick), None)
            self._pick = None
        wanted = wanted or self._key_for(self._symbol) or (keys[0] if keys else None)
        self._symbol_box.set_items(keys, wanted)

        if wanted is None:
            self._symbol = None
            self._series_meta.set_full_text("")
            self._explorer_stack.setCurrentWidget(self._explorer_empty)
            self._sandbox_stack.setCurrentWidget(self._fan_empty)
            return
        self._symbol = wanted
        snapshot = self._snapshot(wanted)
        key = (wanted, snapshot.summary.n_bars, snapshot.summary.last)
        if key != self._loaded_key:
            self._load_series(forecast=True)

    def _watch_row(self, key: str, s: SeriesSnapshot) -> list:
        if math.isnan(s.change_1d):
            change = ("—", PALETTE.muted)
        else:
            change = (f"{s.change_1d:+.2%}", PALETTE.good if s.change_1d >= 0 else PALETTE.critical)
        spark = Sparkline(s.spark) if len(s.spark) >= 2 else label("—", "muted")
        return [key, f"{s.last_close:,.2f}", spark, change]

    def _overview_failed(self, exc: Exception) -> None:
        self._explorer_empty.set_text("Couldn't read the local store", str(exc))
        self._explorer_stack.setCurrentWidget(self._explorer_empty)

    # ------------------------------------------------------- series loading
    def _snapshot(self, key: str) -> SeriesSnapshot:
        return self._series_by_key[key]

    def _on_symbol_chosen(self, symbol: str) -> None:
        if symbol and symbol != self._symbol and self._overview is not None:
            self._symbol = symbol
            self._load_series(forecast=True)

    def _on_window_chosen(self, window: str) -> None:
        if window != self._window:
            self._window = window
            if self._symbol and self._overview is not None:
                self._load_series(forecast=False)

    def _on_watch_row(self, row: int) -> None:
        item = self._watchlist.item(row, 0)
        if item is not None:
            self.select_symbol(item.text())

    def _load_series(self, forecast: bool) -> None:
        snapshot = self._snapshot(self._symbol)
        summary = snapshot.summary
        self._loaded_key = (summary.symbol, summary.n_bars, summary.last)
        self._series_meta.set_full_text(
            f"{summary.bar_size} · {summary.source.upper()} · {summary.n_bars:,} bars")
        self._token += 1
        token = self._token
        window = self._window
        self._tasks.submit(
            lambda: self._workstation.history(summary, window),
            on_done=lambda history: self._render_history(token, history),
            on_error=self._overview_failed,
        )
        if forecast:
            self._tasks.submit(
                lambda: gbm_forecast(self._workstation.history(summary, _FORECAST_WINDOW)),
                on_done=lambda fc: self._render_forecast(token, fc),
                on_error=lambda exc: self._forecast_failed(token, exc),
            )

    def _render_history(self, token: int, history: PriceHistory) -> None:
        if token != self._token:
            return   # superseded by a newer symbol/window choice
        frame = history.frame
        volume = frame["volume"].to_numpy(dtype=float) if "volume" in frame else None
        self._price_chart.set_series(frame.index, frame["close"].to_numpy(dtype=float), volume)
        self._explorer_stack.setCurrentWidget(self._price_chart)

    def _render_forecast(self, token: int, forecast: GBMForecast) -> None:
        if token != self._token:
            return
        est = forecast.estimate
        history = forecast.history
        self._sandbox.set_subtitle(
            f"GBM · {forecast.symbol} 1Y fit · μ̂ {est.mu_hat:+.1%}  σ̂ {est.sigma_hat:.1%}"
            " · illustrative")
        self._fan.set_fan(forecast.dates, forecast.paths, forecast.percentiles,
                          forecast.n_paths, hist_dates=history.frame.index,
                          hist_close=history.closes.to_numpy(dtype=float))
        self._sandbox_stack.setCurrentWidget(self._fan)

    def _forecast_failed(self, token: int, exc: Exception) -> None:
        if token != self._token:
            return
        self._fan_empty.set_text("Couldn't fit a forecast", str(exc))
        self._sandbox_stack.setCurrentWidget(self._fan_empty)
