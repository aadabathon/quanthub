"""Options Lab: one underlying strip and three tabs over the same inputs.

Layout (kept deliberately sparse): the UNDERLYING strip supplies a single
``MarketInputs``, which is manual, synthetic, or IBKR/SQLite-backed. Below
it, one notebook holds three tabs:

* **Pricer**: one contract; price from σ, or implied vol from a price;
  greeks and a greek-vs-spot profile across times to expiry.
* **Strategy**: multi-leg P&L at expiry and at a horizon, exact
  extremes/breakevens, and position greeks.
* **Monte Carlo**: an independent simulation check of the Pricer's contract.

Closed-form work (Pricer, Strategy) runs on the GUI thread; it is
sub-millisecond per solve. Source loads and simulations go through the
TaskRunner. State persists per view via ``ViewStateStore`` under the same
key the old calculator used; its flat keys are migrated on first load.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import PALETTE, SPACE_L, SPACE_M, SPACE_S, SPACE_XS
from quant_hub.gui.views.base import View
from quant_hub.gui.views.options_lab.market_bar import MarketBar
from quant_hub.gui.views.options_lab.monte_carlo_tab import MonteCarloTab
from quant_hub.gui.views.options_lab.pricer_tab import PricerTab
from quant_hub.gui.views.options_lab.strategy_tab import StrategyTab
from quant_hub.services.market_service import MarketService
from quant_hub.storage.state_store import ViewStateStore

_STATE_KEY = "options_lab"


class OptionsLabView(View):
    def __init__(
        self,
        parent: tk.Misc,
        market_service: MarketService,
        tasks: TaskRunner,
        state_store: ViewStateStore,
    ) -> None:
        super().__init__(parent)
        self._state_store = state_store
        self._shown = False
        self._showing_error = False
        saved = _migrate(state_store.load(_STATE_KEY))

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        header = ttk.Frame(self, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=SPACE_L, pady=(SPACE_M, SPACE_XS))
        ttk.Label(header, text="OPTIONS LAB", style="ViewTitle.TLabel").pack(side="left")
        self._message = ttk.Label(header, text="", style="Muted.TLabel")
        self._message.pack(side="right")

        self._market = MarketBar(self, market_service, tasks, saved.get("market", {}),
                                 on_change=self._market_changed, on_recentre=self._recentre,
                                 report=self._report)
        self._market.grid(row=1, column=0, sticky="ew", padx=SPACE_L, pady=(0, SPACE_S))

        self._notebook = ttk.Notebook(self)
        self._notebook.grid(row=2, column=0, sticky="nsew", padx=SPACE_L, pady=(0, SPACE_L))
        self._pricer = PricerTab(self._notebook, self._market.market, saved.get("pricer", {}),
                                 self._report)
        self._strategy = StrategyTab(self._notebook, self._market.market,
                                     saved.get("strategy", {}), self._report)
        self._monte_carlo = MonteCarloTab(self._notebook, self._pricer.inputs, tasks,
                                          saved.get("monte_carlo", {}), self._report)
        for tab, title in ((self._pricer, "Pricer"), (self._strategy, "Strategy"),
                           (self._monte_carlo, "Monte Carlo")):
            self._notebook.add(tab, text=title)
        tabs = self._notebook.tabs()
        index = saved.get("tab", 0)
        self._notebook.select(tabs[index] if isinstance(index, int) and 0 <= index < len(tabs) else tabs[0])
        self._notebook.bind("<<NotebookTabChanged>>", lambda _e: self._refresh_active())

    # -- lifecycle ------------------------------------------------------

    def on_show(self) -> None:
        if not self._shown:
            self._shown = True
            self._refresh_active()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {
            "market": self._market.state(),
            "pricer": self._pricer.state(),
            "strategy": self._strategy.state(),
            "monte_carlo": self._monte_carlo.state(),
            "tab": self._notebook.index(self._notebook.select()),
        })

    # -- coordination ---------------------------------------------------

    def _refresh_active(self) -> None:
        active = self._notebook.nametowidget(self._notebook.select())
        if active is self._pricer:
            self._pricer.refresh()
        elif active is self._strategy:
            self._strategy.refresh()

    def _market_changed(self) -> None:
        self._pricer.refresh()
        self._strategy.refresh()
        self._monte_carlo.mark_stale()

    def _recentre(self, spot: float) -> None:
        self._pricer.recentre(spot)
        self._strategy.recentre(spot)

    def _report(self, message: str, is_error: bool) -> None:
        """One status line for the lab. An empty message clears only errors,
        so a recalculation doesn't wipe a "loaded SPY from ibkr" notice."""
        if not message and not self._showing_error:
            return
        self._showing_error = is_error
        self._message.configure(
            text=message, foreground=PALETTE.critical if is_error else PALETTE.muted
        )


def _migrate(saved: dict) -> dict:
    """Map the old single-card calculator's flat state onto the new sections."""
    if "market" in saved or not saved:
        return saved
    return {
        "market": {"spot": saved.get("spot", "100"), "vol": saved.get("sigma", "20"),
                   "rate": saved.get("rate", "5")},
        "pricer": {"strike": saved.get("strike", "100"), "dte": saved.get("dte", "30"),
                   "option_type": saved.get("option_type", "call")},
    }
