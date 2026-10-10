"""Tkinter composition root: the classic UI on top of the shared core.

``core.build_core`` wires everything below the GUI; this module builds the
Tk views on it. Together with ``app_qt.py`` (the Qt front end, D31) these are
the only modules that know about all layers at once. Every other class
receives its dependencies through its constructor, which is the entire
dependency-injection story of this application.
"""

from __future__ import annotations

import logging
import tkinter as tk

from quant_hub import __version__
from quant_hub.config import AppConfig
from quant_hub.core import build_core
from quant_hub.gui.main_window import MainWindow
from quant_hub.gui.theme import apply_theme
from quant_hub.gui.views.dashboard import DashboardView
from quant_hub.gui.views.garch import GARCHLabView
from quant_hub.gui.views.markets import MarketsView
from quant_hub.gui.views.options_lab import OptionsLabView
from quant_hub.gui.views.placeholder import PlaceholderView
from quant_hub.gui.views.simulator import SimulatorView
from quant_hub.gui.views.volatility_lab import VolatilityLabView

_logger = logging.getLogger(__name__)

_PLACEHOLDERS: list[tuple[str, str]] = [
    ("Kalman", "State-space models and Kalman-filtered estimates."),
    ("Markov", "Regime detection with Markov-switching models."),
    ("Portfolio", "Portfolio analytics, Kelly sizing, and growth optimization."),
    ("Research", "Strategy research and backtesting."),
]


def create_app(config: AppConfig | None = None) -> tuple[tk.Tk, MainWindow]:
    """Build and wire the full application without starting the event loop.

    Split from ``main`` so scripts and smoke tests can drive the assembled
    GUI programmatically.
    """
    core = build_core(config)
    config, bus, broker, store = core.config, core.bus, core.broker, core.store
    state_store, market_service, tasks = core.state_store, core.market_service, core.tasks

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
                    lambda parent: OptionsLabView(parent, market_service, tasks, state_store),
                ),
            ],
        ),
        (
            "MODELS",
            [
                ("GARCH Lab", lambda parent: GARCHLabView(parent, market_service, tasks, state_store)),
                (
                    "Volatility Lab",
                    lambda parent: VolatilityLabView(parent, market_service, tasks, state_store),
                ),
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
        core.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    _logger.info("Quant Hub v%s ready — IBKR disconnected", __version__)
    return root, window


def main() -> None:
    root, _window = create_app()
    root.mainloop()


if __name__ == "__main__":
    main()
