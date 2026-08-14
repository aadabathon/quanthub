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
    FONT_MONO_SM,
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
        store_card = self._card(row, "LOCAL MARKET-DATA STORE", grid=(0, 1))
        ttk.Label(
            store_card, text=str(config.db_path), style="Muted.TLabel"
        ).grid(row=1, column=0, sticky="w", padx=SPACE_S)
        self._store_text = tk.Text(
            store_card, height=12, state="disabled", font=FONT_MONO_SM,
            background=PALETTE.surface, foreground=PALETTE.text_secondary,
            borderwidth=0, highlightthickness=0, padx=SPACE_S, pady=SPACE_S,
        )
        self._store_text.grid(row=2, column=0, sticky="nsew", pady=(4, SPACE_S))
        store_card.rowconfigure(2, weight=1)
        store_card.columnconfigure(0, weight=1)

        bus.subscribe(ConnectionEvent, self._on_connection_event)

    # ------------------------------------------------------------------ layout
    def _card(
        self,
        parent: ttk.Frame,
        title: str,
        grid: tuple[int, int] | None = None,
        top_pad: int = 0,
    ) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(0, 4, 0, 0))
        if grid is not None:
            card.grid(row=grid[0], column=grid[1], sticky="nsew", padx=(SPACE_S, 0))
        else:
            card.pack(fill="x", pady=(top_pad, 0))
        ttk.Label(card, text=title, style="CardHeading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=SPACE_S, pady=(2, 4)
        )
        return card

    # ------------------------------------------------------------------ events
    def on_show(self) -> None:
        self._tasks.submit(self._store.summary, on_done=self._render_store)

    def _on_connection_event(self, event: ConnectionEvent) -> None:
        self._conn_dot.configure(foreground=STATE_COLORS[event.state])
        self._conn_label.configure(text=event.state.value.upper())
        self._conn_detail.configure(text=event.detail)

    def _render_store(self, rows: list[StoredSeriesSummary]) -> None:
        self._store_text.configure(state="normal")
        self._store_text.delete("1.0", "end")
        if not rows:
            self._store_text.insert(
                "end",
                "no persisted bars yet\n\nconnect to IBKR, then use 'Sync history'\n"
                "on the Markets page to populate the store",
            )
        else:
            total = sum(r.n_bars for r in rows)
            self._store_text.insert(
                "end", f"{len(rows)} series · {total:,} bars\n\n"
            )
            for r in rows:
                self._store_text.insert(
                    "end",
                    f"{r.symbol:<8} {r.bar_size:<8} {r.n_bars:>6,} bars   "
                    f"{r.first:%Y-%m-%d} … {r.last:%Y-%m-%d}   {r.source}\n",
                )
        self._store_text.configure(state="disabled")
