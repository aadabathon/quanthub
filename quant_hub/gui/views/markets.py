"""Markets: real market observations with explicit source and provenance.

The page OWNS its data source (the selector at the top) — nothing anywhere
else in the application changes what these rows mean (D23). Every row shows
Last / Mode / As-of / Source, where Mode is the quote's ``MarketDataMode``
(live, delayed, frozen, synthetic). A quote that could not be obtained
renders as an explicit UNAVAILABLE row, and selecting it shows the reason
in the detail line below the table — the log is never the only place the
explanation lives.

Refresh fetches all quotes in one background task, returning per-symbol
results so one unentitled instrument cannot sink the batch.
"""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk

from quant_hub.data.types import Quote
from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import FONT_MONO, PALETTE, SPACE_L, SPACE_S
from quant_hub.gui.views.base import View
from quant_hub.services.market_service import MarketService
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

_DEFAULT_WATCHLIST = ("SPY", "QQQ", "AAPL", "MSFT", "TLT", "GLD")
_STATE_KEY = "markets"


def _fmt_time(ts: datetime) -> str:
    """Time-of-day for today's quotes, full date otherwise."""
    return f"{ts:%H:%M:%S}" if ts.date() == datetime.now().date() else f"{ts:%Y-%m-%d %H:%M}"


class MarketsView(View):
    def __init__(
        self,
        parent: tk.Misc,
        market_service: MarketService,
        tasks: TaskRunner,
        state_store: ViewStateStore,
    ) -> None:
        super().__init__(parent)
        self._market = market_service
        self._tasks = tasks
        self._state_store = state_store
        saved = state_store.load(_STATE_KEY)
        self._symbols: list[str] = list(saved.get("symbols") or _DEFAULT_WATCHLIST)
        self._reasons: dict[str, str] = {}  # symbol -> why unavailable
        self._refreshed_once = False

        # -- controls: source is page-owned and always visible ------------
        controls = ttk.Frame(self, style="Surface.TFrame")
        controls.pack(fill="x", padx=SPACE_L, pady=(14, SPACE_S))

        ttk.Label(controls, text="Data source", style="Secondary.TLabel").pack(side="left")
        names = self._market.source_names
        saved_source = saved.get("source")
        default_source = saved_source if saved_source in names else (
            "ibkr" if "ibkr" in names else names[0]
        )
        self._source_var = tk.StringVar(value=default_source)
        source_box = ttk.Combobox(
            controls, textvariable=self._source_var, values=names,
            state="readonly", width=9,
        )
        source_box.pack(side="left", padx=(6, SPACE_L))
        source_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh())

        ttk.Label(controls, text="Add symbol", style="Muted.TLabel").pack(side="left")
        self._entry_var = tk.StringVar()
        entry = ttk.Entry(controls, textvariable=self._entry_var, width=10, font=FONT_MONO)
        entry.pack(side="left", padx=(6, 8))
        entry.bind("<Return>", lambda _e: self._add_symbol())
        ttk.Button(controls, text="Add", command=self._add_symbol).pack(side="left")

        self._refresh_button = ttk.Button(
            controls, text="Refresh", style="Accent.TButton", command=self._refresh
        )
        self._refresh_button.pack(side="left", padx=(SPACE_L, 0))
        # Minimal history ingestion (persist-only; richer historical research
        # is a later milestone). Keeps the M4 pipeline reachable from the GUI.
        self._sync_button = ttk.Button(
            controls, text="Sync 1Y history", command=self._sync_history
        )
        self._sync_button.pack(side="left", padx=(SPACE_S, 0))
        ttk.Label(
            controls, text="Del key removes the selected row", style="Muted.TLabel"
        ).pack(side="right")

        # -- table --------------------------------------------------------
        columns = ("symbol", "last", "mode", "time", "source")
        self._table = ttk.Treeview(self, columns=columns, show="headings", height=14)
        for col, title, width, anchor, stretch in (
            ("symbol", "Symbol", 90, "w", False),
            ("last", "Last", 110, "e", False),
            ("mode", "Mode", 130, "e", False),
            ("time", "As of", 150, "e", True),
            ("source", "Source", 100, "e", False),
        ):
            self._table.heading(col, text=title, anchor=anchor)
            self._table.column(col, width=width, anchor=anchor, stretch=stretch)
        self._table.pack(fill="both", expand=True, padx=SPACE_L, pady=(0, SPACE_S))
        self._table.bind("<Delete>", self._remove_selected)
        self._table.bind("<<TreeviewSelect>>", self._on_select)
        self._table.tag_configure("unavailable", foreground=PALETTE.muted)

        # -- detail line: the reason a selected row is unavailable --------
        self._detail = ttk.Label(self, text="", style="Muted.TLabel")
        self._detail.pack(fill="x", padx=SPACE_L, pady=(0, 14))

        for symbol in self._symbols:
            self._insert_placeholder_row(symbol)

    # ------------------------------------------------------------------ events
    def on_show(self) -> None:
        if not self._refreshed_once:
            self._refresh()

    def on_hide(self) -> None:
        self._state_store.save(
            _STATE_KEY,
            {"symbols": self._symbols, "source": self._source_var.get()},
        )

    def _on_select(self, _event: object) -> None:
        selection = self._table.selection()
        if not selection:
            self._detail.configure(text="")
            return
        symbol = selection[0]
        self._detail.configure(text=self._reasons.get(symbol, ""))

    # ------------------------------------------------------------------ actions
    def _insert_placeholder_row(self, symbol: str) -> None:
        self._table.insert("", "end", iid=symbol, values=(symbol, "—", "—", "—", "—"))

    def _add_symbol(self) -> None:
        symbol = self._entry_var.get().strip().upper()
        if not symbol or symbol in self._symbols:
            return
        self._symbols.append(symbol)
        self._insert_placeholder_row(symbol)
        self._entry_var.set("")

    def _remove_selected(self, _event: object) -> None:
        for iid in self._table.selection():
            self._table.delete(iid)
            self._symbols.remove(iid)
            self._reasons.pop(iid, None)

    def _refresh(self) -> None:
        self._refreshed_once = True
        symbols = list(self._symbols)
        source = self._source_var.get()
        self._refresh_button.configure(state="disabled")
        self._detail.configure(text=f"refreshing from {source}…")

        def fetch() -> list[tuple[str, Quote | Exception]]:
            results: list[tuple[str, Quote | Exception]] = []
            for symbol in symbols:
                try:
                    results.append((symbol, self._market.get_quote(symbol, source=source)))
                except Exception as exc:
                    results.append((symbol, exc))
            return results

        self._tasks.submit(fetch, on_done=self._on_quotes, on_error=self._on_error)

    def _sync_history(self) -> None:
        """Fetch+persist one year of daily bars for the selected symbol.

        Deliberately persist-only: the outcome is reported here and in the
        log/Dashboard store card; charting persisted history is a later
        milestone.
        """
        selection = self._table.selection()
        if not selection:
            self._detail.configure(text="select a row to sync its daily history")
            return
        symbol = selection[0]
        source = self._source_var.get()
        end = datetime.now()
        start = end - timedelta(days=365)
        self._sync_button.configure(state="disabled")
        self._detail.configure(text=f"syncing {symbol} daily history from {source}…")

        def work() -> str:
            history = self._market.get_history(symbol, start, end, source=source)
            return (
                f"{symbol}: {len(history)} bars available "
                f"{history.start:%Y-%m-%d} → {history.end:%Y-%m-%d} ({source})"
            )

        def done(text: str) -> None:
            self._sync_button.configure(state="normal")
            self._detail.configure(text=text)

        def failed(exc: Exception) -> None:
            self._sync_button.configure(state="normal")
            self._detail.configure(text=f"sync failed — {exc}")
            _logger.error("history sync failed for %s: %s", symbol, exc)

        self._tasks.submit(work, on_done=done, on_error=failed)

    # ------------------------------------------------------------------ results
    def _on_quotes(self, results: list[tuple[str, Quote | Exception]]) -> None:
        self._refresh_button.configure(state="normal")
        unavailable = 0
        for symbol, outcome in results:
            if not self._table.exists(symbol):
                continue  # removed while the fetch ran
            if isinstance(outcome, Quote):
                self._reasons.pop(symbol, None)
                self._table.item(
                    symbol,
                    values=(
                        symbol,
                        f"{outcome.price:,.2f}",
                        outcome.mode.value.upper(),
                        _fmt_time(outcome.timestamp),
                        outcome.source.upper(),
                    ),
                    tags=(),
                )
            else:
                unavailable += 1
                self._reasons[symbol] = f"{symbol}: {outcome}"
                self._table.item(
                    symbol,
                    values=(symbol, "—", "UNAVAILABLE", "—", self._source_var.get().upper()),
                    tags=("unavailable",),
                )
                _logger.warning("quote unavailable for %s: %s", symbol, outcome)
        self._detail.configure(
            text=(
                f"{unavailable} symbol(s) unavailable — select a row for the reason"
                if unavailable
                else ""
            )
        )

    def _on_error(self, exc: Exception) -> None:
        self._refresh_button.configure(state="normal")
        self._detail.configure(text=f"refresh failed: {exc}")
        _logger.error("watchlist refresh failed: %s", exc)
