"""Options Lab: one underlying card and three tabs over the same inputs.

The underlying card supplies a single ``MarketInputs`` (manual, synthetic,
or IBKR/SQLite-backed). The Pricer and Strategy tabs recompute from it as
you type (closed form, GUI thread); the Monte Carlo tab prices the Pricer's
contract on the TaskRunner and is only marked stale when inputs change.

State persists under the Tk page's key, so inputs carry across both UIs;
the old single-card calculator's flat keys are migrated on first load.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout

from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui_qt.components import PageHeader, PageStack, SegmentedControl
from quant_hub.gui_qt.pages.base import ScrollPage
from quant_hub.gui_qt.pages.options_lab.monte_carlo import MonteCarloTab
from quant_hub.gui_qt.pages.options_lab.pricer import PricerTab
from quant_hub.gui_qt.pages.options_lab.strategy import StrategyTab
from quant_hub.gui_qt.pages.options_lab.underlying import UnderlyingCard
from quant_hub.services.market_service import MarketService
from quant_hub.storage.state_store import ViewStateStore

_STATE_KEY = "options_lab"
_TABS = ("Pricer", "Strategy", "Monte Carlo")


class OptionsLabPage(ScrollPage):
    def __init__(self, market_service: MarketService, tasks: TaskRunner,
                 state_store: ViewStateStore) -> None:
        super().__init__()
        self._state_store = state_store
        self._shown = False
        self._showing_error = False
        saved = _migrate(state_store.load(_STATE_KEY))

        self._header = PageHeader(
            "Options Lab",
            "European options on one underlying: pricing and implied vol, multi-leg "
            "strategies, and a Monte Carlo check")
        self.body.addWidget(self._header)
        self._underlying = UnderlyingCard(market_service, tasks, saved.get("market", {}),
                                          self._report)
        self.body.addWidget(self._underlying)

        self._pricer = PricerTab(self._underlying.market, saved.get("pricer", {}), self._report)
        self._strategy = StrategyTab(self._underlying.market, saved.get("strategy", {}),
                                     self._report)
        self._monte_carlo = MonteCarloTab(self._pricer.inputs, tasks,
                                          saved.get("monte_carlo", {}), self._report)
        self._stack = PageStack()
        for tab in (self._pricer, self._strategy, self._monte_carlo):
            self._stack.addWidget(tab)
        index = saved.get("tab", 0)
        current = _TABS[index] if isinstance(index, int) and 0 <= index < len(_TABS) else _TABS[0]
        tabs_row = QHBoxLayout()
        self._tabs = SegmentedControl(list(_TABS), current)
        self._tabs.changed.connect(self._on_tab)
        tabs_row.addWidget(self._tabs)
        tabs_row.addStretch(1)
        self.body.addLayout(tabs_row)
        self.body.addWidget(self._stack, 1)
        self._stack.setCurrentIndex(_TABS.index(current))

        self._underlying.changed.connect(self._market_changed)
        self._underlying.recentre.connect(self._recentre)

    # ----------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        if not self._shown:
            self._shown = True
            self._refresh_active()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {
            "market": self._underlying.state(), "pricer": self._pricer.state(),
            "strategy": self._strategy.state(), "monte_carlo": self._monte_carlo.state(),
            "tab": self._stack.currentIndex(),
        })

    # -------------------------------------------------------- coordination
    def _on_tab(self, name: str) -> None:
        self._stack.setCurrentIndex(_TABS.index(name))
        self._refresh_active()

    def _refresh_active(self) -> None:
        active = self._stack.currentWidget()
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
        so a recalculation doesn't wipe a "Loaded SPY from ibkr" notice."""
        if not message and not self._showing_error:
            return
        self._showing_error = is_error
        self._header.set_status(message, error=is_error)


def _migrate(saved: dict) -> dict:
    """Map the old single-card calculator's flat state onto the sections."""
    if "market" in saved or not saved:
        return saved
    return {
        "market": {"spot": saved.get("spot", "100"), "vol": saved.get("sigma", "20"),
                   "rate": saved.get("rate", "5")},
        "pricer": {"strike": saved.get("strike", "100"), "dte": saved.get("dte", "30"),
                   "option_type": saved.get("option_type", "call")},
    }
