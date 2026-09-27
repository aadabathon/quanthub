"""Composition root: constructs and wires every component, then runs Tkinter.

This is deliberately the only module that knows about all layers at once.
Every other class receives its dependencies through its constructor, which is
the entire dependency-injection story of this application.
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk

from quant_hub import __version__
from quant_hub.broker.ibkr import IBKRBroker
from quant_hub.broker.market_source import IBKRSource
from quant_hub.config import AppConfig
from quant_hub.data.sources import SyntheticSource
from quant_hub.events import EventBus, EventBusLogHandler
from quant_hub.services.cached_source import CachedSource
from quant_hub.storage.bar_store import BarStore
from quant_hub.storage.state_store import ViewStateStore
from quant_hub.gui.main_window import MainWindow
from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import apply_theme
from quant_hub.gui.views.dashboard import DashboardView
from quant_hub.gui.views.garch import GARCHLabView
from quant_hub.gui.views.markets import MarketsView
from quant_hub.gui.views.options_lab import OptionsLabView
from quant_hub.gui.views.placeholder import PlaceholderView
from quant_hub.gui.views.simulator import SimulatorView
from quant_hub.services.market_service import MarketService

_logger = logging.getLogger(__name__)

_PLACEHOLDERS: list[tuple[str, str]] = [
    ("Volatility", "Implied-volatility estimation, smiles, and surfaces."),
    ("Kalman", "State-space models and Kalman-filtered estimates."),
    ("Markov", "Regime detection with Markov-switching models."),
    ("Portfolio", "Portfolio analytics, Kelly sizing, and growth optimization."),
    ("Research", "Strategy research and backtesting."),
]


def _setup_logging(bus: EventBus) -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Windowed launches (the gui-scripts entry point, i.e. double-clicking
    # the app) have no console: sys.stderr is None, so a StreamHandler here
    # would target nothing. The event-bus handler below (-> Log panel) is
    # the real log surface for that case; console output is a `python -m
    # quant_hub` debugging convenience only.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
        root_logger.addHandler(console)

    bus_handler = EventBusLogHandler(bus)
    bus_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(bus_handler)

    # ibapi's own loggers narrate every request/response at INFO; that
    # belongs in a wire trace, not the GUI log panel (observed against live
    # TWS, 2026-08-14). Our broker module logs the meaningful events itself.
    logging.getLogger("ibapi").setLevel(logging.WARNING)


def create_app(config: AppConfig | None = None) -> tuple[tk.Tk, MainWindow]:
    """Build and wire the full application without starting the event loop.

    Split from ``main`` so scripts and smoke tests can drive the assembled
    GUI programmatically.
    """
    config = config or AppConfig()
    bus = EventBus()
    _setup_logging(bus)

    broker = IBKRBroker(config, bus)
    store = BarStore(config.db_path)
    # Same db file as the bar store — just a second, unrelated table. Each
    # view owns its own state shape as an opaque JSON blob (see module doc).
    state_store = ViewStateStore(config.db_path)
    # IBKR is wrapped in the cache; synthetic stays direct (generated
    # experiments are reproducible from their parameters — persistence is
    # for acquired observations only). No active source: every caller names
    # its source explicitly (D23).
    market_service = MarketService(
        sources=[SyntheticSource(), CachedSource(IBKRSource(broker), store)],
    )
    tasks = TaskRunner()

    root = tk.Tk()
    root.title("Quant Hub")
    root.geometry("1280x800")
    root.minsize(1000, 640)
    apply_theme(root)

    view_sections = [
        (
            None,
            [
                (
                    "Dashboard",
                    lambda parent: DashboardView(
                        parent, broker.state, store, tasks, bus, config
                    ),
                ),
                (
                    "Markets",
                    lambda parent: MarketsView(parent, market_service, tasks, state_store),
                ),
                (
                    "Simulator",
                    lambda parent: SimulatorView(parent, tasks, config, state_store),
                ),
                (
                    "Options Lab",
                    lambda parent: OptionsLabView(parent, state_store),
                ),
            ],
        ),
        (
            "MODELS",
            [
                ("GARCH Lab", lambda parent: GARCHLabView(parent, market_service, tasks, state_store)),
                *[
                    (title, lambda parent, t=title, d=desc: PlaceholderView(parent, t, d))
                    for title, desc in _PLACEHOLDERS
                ],
            ],
        ),
    ]

    window = MainWindow(
        root,
        view_sections=view_sections,
        broker=broker,
        tasks=tasks,
        bus=bus,
        pump_interval_ms=config.pump_interval_ms,
    )

    def on_close() -> None:
        _logger.info("shutting down")
        # The active view never gets on_hide from a tab switch it didn't
        # make — give it one now so its state is saved before we close.
        try:
            window.shutdown()
        except Exception:
            _logger.exception("error saving view state at shutdown")
        tasks.shutdown()
        try:
            broker.disconnect()
        except Exception:
            _logger.exception("error disconnecting broker at shutdown")
        try:
            store.close()
        except Exception:
            _logger.exception("error closing bar store at shutdown")
        try:
            state_store.close()
        except Exception:
            _logger.exception("error closing view-state store at shutdown")
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    _logger.info("Quant Hub v%s ready — IBKR disconnected", __version__)
    return root, window


def main() -> None:
    root, _window = create_app()
    root.mainloop()


if __name__ == "__main__":
    main()
