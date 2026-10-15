"""Composition root: the GUI on top of the shared core.

``core.build_core`` wires everything below the GUI; this module builds the
window and its pages on it. Together they are the only code that knows
about all layers at once — every other class receives its dependencies
through its constructor, which is the entire dependency-injection story.
``quant-hub`` (the desktop shortcut) and ``python -m quant_hub`` run
``main``.
"""

from __future__ import annotations

import logging
import sys

import quant_hub.gui  # noqa: F401  (pins qtpy to PySide6 before qtawesome loads)
from PySide6.QtWidgets import QApplication

from quant_hub import __version__
from quant_hub.config import AppConfig
from quant_hub.core import Core, build_core
from quant_hub.gui.main_window import MainWindow, PageSections
from quant_hub.gui.pages.dashboard import DashboardPage
from quant_hub.gui.pages.garch_lab import GARCHLabPage
from quant_hub.gui.pages.kalman_lab import KalmanLabPage
from quant_hub.gui.pages.markets import MarketsPage
from quant_hub.gui.pages.markov_lab import MarkovLabPage
from quant_hub.gui.pages.options_lab import OptionsLabPage
from quant_hub.gui.pages.placeholder import PlaceholderPage
from quant_hub.gui.pages.simulator import SimulatorPage
from quant_hub.gui.pages.volatility_lab import VolatilityLabPage
from quant_hub.gui.theme import apply_theme

_logger = logging.getLogger(__name__)

# Planned roadmap modules: (icon, description). Placeholders until built.
_PLANNED: dict[str, tuple[str, str]] = {
    "Portfolio": ("ph.chart-pie-slice", "Portfolio analytics, Kelly sizing, growth."),
    "Research": ("ph.file-text", "Strategy research and backtesting."),
}


def _planned(name: str):
    icon_name, description = _PLANNED[name]
    return (name, icon_name, lambda _shell: PlaceholderPage(name, icon_name, description))


def _sections(core: Core) -> PageSections:
    dashboard = ("Dashboard", "ph.house", lambda shell: DashboardPage(
        shell, core.workstation, core.tasks, core.state_store, core.config))
    markets = ("Markets", "ph.chart-bar", lambda shell: MarketsPage(
        shell, core.market_service, core.workstation, core.tasks, core.state_store))
    garch = ("GARCH Lab", "ph.activity", lambda _shell: GARCHLabPage(
        core.market_service, core.tasks, core.state_store))
    simulator = ("Simulator", "ph.trend-up", lambda _shell: SimulatorPage(
        core.tasks, core.config, core.state_store))
    options = ("Options Lab", "ph.calculator", lambda _shell: OptionsLabPage(
        core.market_service, core.chains, core.tasks, core.state_store))
    vol_lab = ("Volatility Lab", "mdi6.sigma", lambda _shell: VolatilityLabPage(
        core.market_service, core.chains, core.tasks, core.state_store))
    kalman = ("Kalman Lab", "ph.sliders-horizontal", lambda _shell: KalmanLabPage(
        core.market_service, core.tasks, core.state_store))
    markov = ("Markov Lab", "ph.share-network", lambda _shell: MarkovLabPage(
        core.market_service, core.tasks, core.state_store))
    return [
        (None, [dashboard, markets, simulator, options]),
        ("Research", [garch, vol_lab, kalman, markov, *(_planned(n) for n in _PLANNED)]),
    ]


def create_app(config: AppConfig | None = None,
               argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    """Build and wire the application without entering the event loop
    (smoke tests and screenshot scripts drive it from here)."""
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Quant Hub")
    apply_theme(app)
    core = build_core(config)

    window = MainWindow(core, _sections(core))
    window.status.set_store_state("Local store ready", ok=True)
    # Open at most of the screen (a workstation), capped so very large
    # monitors still get a window rather than a wall, and centered.
    available = app.primaryScreen().availableGeometry()
    window.setMinimumSize(1100, 700)
    window.resize(min(1920, int(available.width() * 0.88)),
                  min(1240, int(available.height() * 0.88)))
    window.move(available.center() - window.rect().center())

    def on_quit() -> None:
        # One-shot: QApplication is a process singleton, so a later
        # create_app (smoke tests) must not re-run this core's shutdown.
        app.aboutToQuit.disconnect(on_quit)
        _logger.info("shutting down")
        try:
            window.shutdown()
        except Exception:
            _logger.exception("error saving page state at shutdown")
        core.shutdown()

    app.aboutToQuit.connect(on_quit)
    _logger.info("Quant Hub v%s ready — %s", __version__,
                 "IBKR disconnected" if core.config.ibkr_enabled else "IBKR off in settings")
    return app, window


def main() -> None:
    app, window = create_app()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
