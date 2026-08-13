"""Markets view: a small quote watchlist over the active data source.

Refresh fetches all quotes in one background task (they are fast on the
synthetic source and serialized anyway on the single task worker), returning
per-symbol results so one bad symbol cannot sink the batch.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from quant_hub.data.types import Quote
from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import FONT_MONO, PALETTE
from quant_hub.gui.views.base import View
from quant_hub.services.market_service import MarketService

_logger = logging.getLogger(__name__)

_DEFAULT_WATCHLIST = ("SPY", "QQQ", "AAPL", "MSFT", "TLT", "GLD")


class MarketsView(View):
    def __init__(
        self, parent: tk.Misc, market_service: MarketService, tasks: TaskRunner
    ) -> None:
        super().__init__(parent)
        self._market = market_service
        self._tasks = tasks
        self._symbols: list[str] = list(_DEFAULT_WATCHLIST)
        self._refreshed_once = False

        controls = ttk.Frame(self, style="Surface.TFrame")
        controls.pack(fill="x", padx=16, pady=(14, 8))

        ttk.Label(controls, text="Add symbol", style="Muted.TLabel").pack(side="left")
        self._entry_var = tk.StringVar()
        entry = ttk.Entry(controls, textvariable=self._entry_var, width=10, font=FONT_MONO)
        entry.pack(side="left", padx=(6, 8))
        entry.bind("<Return>", lambda _e: self._add_symbol())
        ttk.Button(controls, text="Add", command=self._add_symbol).pack(side="left")

        self._refresh_button = ttk.Button(
            controls, text="Refresh", style="Accent.TButton", command=self._refresh
        )
        self._refresh_button.pack(side="left", padx=(16, 0))
        ttk.Label(
            controls, text="Del key removes the selected row", style="Muted.TLabel"
        ).pack(side="right")

        columns = ("symbol", "price", "time", "source")
        self._table = ttk.Treeview(self, columns=columns, show="headings", height=14)
        for col, title, width, anchor in (
            ("symbol", "Symbol", 90, "w"),
            ("price", "Last", 110, "e"),
            ("time", "As of", 160, "e"),
            ("source", "Source", 100, "e"),
        ):
            self._table.heading(col, text=title, anchor=anchor)
            self._table.column(col, width=width, anchor=anchor, stretch=(col == "time"))
        self._table.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self._table.bind("<Delete>", self._remove_selected)
        self._table.tag_configure("error", foreground=PALETTE.critical)

        for symbol in self._symbols:
            self._table.insert("", "end", iid=symbol, values=(symbol, "—", "—", "—"))

    def on_show(self) -> None:
        if not self._refreshed_once:
            self._refresh()

    # ------------------------------------------------------------------ actions
    def _add_symbol(self) -> None:
        symbol = self._entry_var.get().strip().upper()
        if not symbol or symbol in self._symbols:
            return
        self._symbols.append(symbol)
        self._table.insert("", "end", iid=symbol, values=(symbol, "—", "—", "—"))
        self._entry_var.set("")

    def _remove_selected(self, _event: object) -> None:
        for iid in self._table.selection():
            self._table.delete(iid)
            self._symbols.remove(iid)

    def _refresh(self) -> None:
        self._refreshed_once = True
        symbols = list(self._symbols)
        self._refresh_button.configure(state="disabled")

        def fetch() -> list[tuple[str, Quote | Exception]]:
            results: list[tuple[str, Quote | Exception]] = []
            for symbol in symbols:
                try:
                    results.append((symbol, self._market.get_quote(symbol)))
                except Exception as exc:
                    results.append((symbol, exc))
            return results

        self._tasks.submit(fetch, on_done=self._on_quotes, on_error=self._on_error)

    # ------------------------------------------------------------------ results
    def _on_quotes(self, results: list[tuple[str, Quote | Exception]]) -> None:
        self._refresh_button.configure(state="normal")
        for symbol, outcome in results:
            if not self._table.exists(symbol):
                continue  # removed while the fetch ran
            if isinstance(outcome, Quote):
                self._table.item(
                    symbol,
                    values=(
                        symbol,
                        f"{outcome.price:,.2f}",
                        f"{outcome.timestamp:%Y-%m-%d %H:%M}",
                        outcome.source,
                    ),
                    tags=(),
                )
            else:
                self._table.item(
                    symbol, values=(symbol, "error", "—", "—"), tags=("error",)
                )
                _logger.warning("quote failed for %s: %s", symbol, outcome)

    def _on_error(self, exc: Exception) -> None:
        self._refresh_button.configure(state="normal")
        _logger.error("watchlist refresh failed: %s", exc)
