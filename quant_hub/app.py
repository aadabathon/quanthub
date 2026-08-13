"""Composition root: constructs and wires every component, then runs Tkinter.

This is deliberately the only module that knows about all layers at once.
Every other class receives its dependencies through its constructor, which is
the entire dependency-injection story of this application.
"""

from __future__ import annotations

import logging
import tkinter as tk

from quant_hub import __version__
from quant_hub.broker.ibkr import IBKRBroker
from quant_hub.broker.market_source import IBKRSource
from quant_hub.config import AppConfig
from quant_hub.data.sources import SyntheticSource
from quant_hub.events import EventBus, EventBusLogHandler
from quant_hub.gui.main_window import MainWindow
from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import apply_theme
from quant_hub.gui.views.dashboard import DashboardView
from quant_hub.gui.views.markets import MarketsView
from quant_hub.gui.views.placeholder import PlaceholderView
from quant_hub.services.market_service import MarketService

_logger = logging.getLogger(__name__)

_PLACEHOLDERS: list[tuple[str, str]] = [
    ("Options Lab", "Options-chain analysis, Black-Scholes pricing, and greeks."),
    ("Volatility", "Implied-volatility estimation, smiles, and surfaces."),
    ("Kalman", "State-space models and Kalman-filtered estimates."),
    ("GARCH", "Conditional-volatility modeling and forecasts."),
    ("Markov", "Regime detection with Markov-switching models."),
    ("Portfolio", "Portfolio analytics, Kelly sizing, and growth optimization."),
    ("Research", "Strategy research and backtesting."),
]


def _setup_logging(bus: EventBus) -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    root_logger.addHandler(console)

    bus_handler = EventBusLogHandler(bus)
    bus_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(bus_handler)


def create_app(config: AppConfig | None = None) -> tuple[tk.Tk, MainWindow]:
    """Build and wire the full application without starting the event loop.

    Split from ``main`` so scripts and smoke tests can drive the assembled
    GUI programmatically.
    """
    config = config or AppConfig()
    bus = EventBus()
    _setup_logging(bus)

    broker = IBKRBroker(config, bus)
    market_service = MarketService(
        sources=[SyntheticSource(), IBKRSource(broker)],
        active="synthetic",
    )
    tasks = TaskRunner()

    root = tk.Tk()
    root.title("Quant Hub")
    root.geometry("1280x800")
    root.minsize(1000, 640)
    apply_theme(root)

    view_specs = [
        (
            "Dashboard",
            lambda parent: DashboardView(parent, market_service, tasks, config),
        ),
        ("Markets", lambda parent: MarketsView(parent, market_service, tasks)),
    ] + [
        (title, lambda parent, t=title, d=desc: PlaceholderView(parent, t, d))
        for title, desc in _PLACEHOLDERS
    ]

    window = MainWindow(
        root,
        view_specs=view_specs,
        broker=broker,
        tasks=tasks,
        bus=bus,
        pump_interval_ms=config.pump_interval_ms,
    )

    def on_close() -> None:
        _logger.info("shutting down")
        tasks.shutdown()
        try:
            broker.disconnect()
        except Exception:
            _logger.exception("error disconnecting broker at shutdown")
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    _logger.info(
        "Quant Hub v%s ready — data source: synthetic, IBKR disconnected", __version__
    )
    return root, window


def main() -> None:
    root, _window = create_app()
    root.mainloop()


if __name__ == "__main__":
    main()
