"""Dashboard: the workstation overview (M6, D24 — no longer the GBM lab).

A deliberately *static* set of informative cards: connection health, the
local market-data store, and where things live. No widget framework, no
layout persistence, no drag-and-drop — those are documented future
enhancements; today the page simply answers "what is the state of my
workstation" at a glance. Cards that would need substantial new
infrastructure (live watchlist snapshots, model summaries) are omitted
rather than faked.

Threading: the store summary is fetched through the TaskRunner on every
``on_show``; connection state arrives as ``ConnectionEvent``s dispatched on
the GUI thread by the pump.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from quant_hub import __version__
from quant_hub.broker.base import ConnectionEvent, ConnectionState
from quant_hub.config import AppConfig
from quant_hub.events import EventBus
from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import (
    FONT_MONO,
    PALETTE,
    SPACE_L,
    SPACE_M,
    SPACE_S,
)
from quant_hub.gui.views.base import View
from quant_hub.gui.widgets.status_bar import STATE_COLORS
from quant_hub.storage.bar_store import BarStore, StoredSeriesSummary

_PAGE_GUIDE = (
    ("Markets", "real instruments: quotes with source, freshness, and provenance"),
    ("Simulator", "controlled stochastic experiments (GBM + Monte Carlo)"),
    ("Model pages", "planned specialized workflows — placeholders until built"),
)


class DashboardView(View):
    def __init__(
        self,
        parent: tk.Misc,
        broker_state: ConnectionState,
        store: BarStore,
        tasks: TaskRunner,
        bus: EventBus,
        config: AppConfig,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._tasks = tasks
        self._config = config

        header = ttk.Frame(self, style="Surface.TFrame")
        header.pack(fill="x", padx=SPACE_L, pady=(SPACE_M, SPACE_S))
        ttk.Label(header, text="Workstation Overview", style="ViewTitle.TLabel").pack(
            side="left"
        )
        ttk.Label(header, text=f"Quant Hub v{__version__}", style="Muted.TLabel").pack(
            side="right", pady=(4, 0)
        )

        # -- KPI row: the headline numbers, at a glance ---------------------
        kpi_row = ttk.Frame(self, style="Surface.TFrame")
        kpi_row.pack(fill="x", padx=SPACE_L, pady=(0, SPACE_S))
        self._tile_series = self._stat_tile(kpi_row, "SERIES TRACKED")
        self._tile_bars = self._stat_tile(kpi_row, "BARS PERSISTED")
        self._tile_synced = self._stat_tile(kpi_row, "LAST SYNCED", last=True)

        row = ttk.Frame(self, style="Surface.TFrame")
        row.pack(fill="both", expand=True, padx=SPACE_L, pady=(0, SPACE_L))
        row.columnconfigure(0, weight=1, uniform="cards")
        row.columnconfigure(1, weight=2, uniform="cards")
        row.rowconfigure(0, weight=1)

        # -- left column: connection + orientation ------------------------
        left = ttk.Frame(row, style="Surface.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, SPACE_S))

        conn = self._card(left, "IBKR CONNECTION")
        self._conn_dot = tk.Label(
            conn, text="●", background=PALETTE.surface,
            foreground=STATE_COLORS[broker_state], font=FONT_MONO,
        )
        self._conn_dot.grid(row=1, column=0, sticky="w", padx=(SPACE_S, 4))
        self._conn_label = ttk.Label(
            conn, text=broker_state.value.upper(), style="Mono.TLabel"
        )
        self._conn_label.grid(row=1, column=1, sticky="w")
        ttk.Label(
            conn,
            text=f"{config.ibkr_host}:{config.ibkr_port} · read-only API",
            style="Muted.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=SPACE_S, pady=(2, SPACE_S))
        self._conn_detail = ttk.Label(conn, text="", style="Muted.TLabel", wraplength=280)
        self._conn_detail.grid(row=3, column=0, columnspan=2, sticky="w",
                               padx=SPACE_S, pady=(0, SPACE_S))

        guide = self._card(left, "WHERE THINGS LIVE", top_pad=SPACE_S)
        for i, (page, blurb) in enumerate(_PAGE_GUIDE, start=1):
            ttk.Label(guide, text=page, style="Secondary.TLabel").grid(
                row=i, column=0, sticky="nw", padx=(SPACE_S, 8), pady=2
            )
            ttk.Label(guide, text=blurb, style="Muted.TLabel", wraplength=230).grid(
                row=i, column=1, sticky="w", pady=2
            )

        # -- right column: persisted market data --------------------------
        # sticky="new" (not "nsew"): this card sizes to its content -- a
        # short table shouldn't be stretched to match the left column's
        # height, which just leaves a dead band inside the border.
        store_card = self._card(row, "LOCAL MARKET-DATA STORE", grid=(0, 1), sticky="new")
        store_card.columnconfigure(0, weight=1)
        ttk.Label(
            store_card, text=str(config.db_path), style="Muted.TLabel"
        ).grid(row=1, column=0, sticky="w", padx=SPACE_S, pady=(0, SPACE_S))

        columns = ("symbol", "bar_size", "n_bars", "range", "source")
        self._store_table = ttk.Treeview(
            store_card, columns=columns, show="headings", height=1
        )
        for col, title, width, anchor in (
            ("symbol", "Symbol", 80, "w"),
            ("bar_size", "Bar size", 70, "w"),
            ("n_bars", "Bars", 70, "e"),
            ("range", "Range", 190, "w"),
            ("source", "Source", 80, "e"),
        ):
            self._store_table.heading(col, text=title, anchor=anchor)
            self._store_table.column(col, width=width, anchor=anchor, stretch=False)
        # sticky="w" (not "ew"): natural width -- a stretched column would
        # otherwise open a wide gap before "Source" on a roomy window.
        self._store_table.grid(row=2, column=0, sticky="w", padx=SPACE_S)
        self._store_table.grid_remove()  # shown once _render_store has rows

        self._store_empty = ttk.Label(
            store_card,
            text="no persisted bars yet\n\n"
            "connect to IBKR, then use 'Sync 1Y history' on the Markets "
            "page to populate the store",
            style="Muted.TLabel",
        )
        self._store_empty.grid(row=2, column=0, sticky="w", padx=SPACE_S, pady=(0, SPACE_S))

        bus.subscribe(ConnectionEvent, self._on_connection_event)

    # ------------------------------------------------------------------ layout
    def _card(
        self,
        parent: ttk.Frame,
        title: str,
        grid: tuple[int, int] | None = None,
        top_pad: int = 0,
        sticky: str = "nsew",
    ) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(0, 4, 0, 0))
        if grid is not None:
            card.grid(row=grid[0], column=grid[1], sticky=sticky, padx=(SPACE_S, 0))
        else:
            card.pack(fill="x", pady=(top_pad, 0))
        ttk.Label(card, text=title, style="CardHeading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=SPACE_S, pady=(2, 4)
        )
        return card

    def _stat_tile(self, parent: ttk.Frame, label: str, last: bool = False) -> ttk.Label:
        """A KPI stat tile: uppercase label + a large value, filled in later."""
        tile = ttk.Frame(parent, style="Card.TFrame", padding=(SPACE_M, SPACE_S))
        tile.pack(side="left", fill="both", expand=True, padx=(0, 0 if last else SPACE_S))
        ttk.Label(tile, text=label, style="CardHeading.TLabel").pack(anchor="w")
        value = ttk.Label(tile, text="—", style="Hero.TLabel")
        value.pack(anchor="w", pady=(2, 0))
        return value

    # ------------------------------------------------------------------ events
    def on_show(self) -> None:
        self._tasks.submit(self._store.summary, on_done=self._render_store)

    def _on_connection_event(self, event: ConnectionEvent) -> None:
        self._conn_dot.configure(foreground=STATE_COLORS[event.state])
        self._conn_label.configure(text=event.state.value.upper())
        self._conn_detail.configure(text=event.detail)

    def _render_store(self, rows: list[StoredSeriesSummary]) -> None:
        if not rows:
            self._tile_series.configure(text="0")
            self._tile_bars.configure(text="0")
            self._tile_synced.configure(text="—")
            self._store_table.grid_remove()
            self._store_empty.grid()
            return

        total_bars = sum(r.n_bars for r in rows)
        last_synced = max(r.last for r in rows)
        self._tile_series.configure(text=str(len(rows)))
        self._tile_bars.configure(text=f"{total_bars:,}")
        self._tile_synced.configure(text=f"{last_synced:%Y-%m-%d}")

        self._store_table.delete(*self._store_table.get_children())
        for r in rows:
            self._store_table.insert(
                "", "end",
                values=(
                    r.symbol, r.bar_size, f"{r.n_bars:,}",
                    f"{r.first:%Y-%m-%d} … {r.last:%Y-%m-%d}", r.source.upper(),
                ),
            )
        self._store_table.configure(height=min(len(rows), 10))
        self._store_empty.grid_remove()
        self._store_table.grid()
