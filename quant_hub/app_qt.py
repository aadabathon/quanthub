"""Qt composition root: the new front end on top of the shared core (D31).

Pages not yet ported render a placeholder pointing at the classic Tk UI,
which keeps working (``py -3.12 -m quant_hub --classic``, or the
``quant-hub-classic`` launcher) until the Qt UI reaches parity; then
``gui/`` and ``app.py`` are deleted. ``quant-hub`` / ``python -m quant_hub``
launch this module.
"""

from __future__ import annotations

import logging
import sys

import quant_hub.gui_qt  # noqa: F401  (pins qtpy to PySide6 before qtawesome loads)
from PySide6.QtWidgets import QApplication

from quant_hub import __version__
from quant_hub.config import AppConfig
from quant_hub.core import Core, build_core
from quant_hub.gui_qt.main_window import MainWindow, PageSections
from quant_hub.gui_qt.pages.dashboard import DashboardPage
from quant_hub.gui_qt.pages.garch_lab import GARCHLabPage
from quant_hub.gui_qt.pages.markets import MarketsPage
from quant_hub.gui_qt.pages.options_lab import OptionsLabPage
from quant_hub.gui_qt.pages.placeholder import PlaceholderPage
from quant_hub.gui_qt.pages.simulator import SimulatorPage
from quant_hub.gui_qt.pages.volatility_lab import VolatilityLabPage
from quant_hub.gui_qt.theme import apply_theme

_logger = logging.getLogger(__name__)

# (name, icon, description, ported later? — False means a planned module)
_PENDING: dict[str, tuple[str, str, bool]] = {
    "Kalman": ("ph.sliders-horizontal", "State-space models and Kalman-filtered estimates.", False),
    "Markov": ("ph.share-network", "Regime detection with Markov-switching models.", False),
    "Portfolio": ("ph.chart-pie-slice", "Portfolio analytics, Kelly sizing, growth.", False),
    "Research": ("ph.file-text", "Strategy research and backtesting.", False),
}


def _pending(name: str):
    icon_name, description, ported_later = _PENDING[name]
    return (name, icon_name,
            lambda _shell: PlaceholderPage(name, icon_name, description, ported_later))


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
        core.market_service, core.tasks, core.state_store))
    vol_lab = ("Volatility Lab", "mdi6.sigma", lambda _shell: VolatilityLabPage(
        core.market_service, core.tasks, core.state_store))
    return [
        (None, [dashboard, markets, simulator, options]),
        ("Research", [garch, vol_lab, *(_pending(n) for n in ("Kalman", "Markov", "Portfolio",
                                                              "Research"))]),
    ]


def create_qt_app(config: AppConfig | None = None,
                  argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    """Build and wire the Qt application without entering the event loop
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
        # create_qt_app (smoke tests) must not re-run this core's shutdown.
        app.aboutToQuit.disconnect(on_quit)
        _logger.info("shutting down")
        try:
            window.shutdown()
        except Exception:
            _logger.exception("error saving page state at shutdown")
        core.shutdown()

    app.aboutToQuit.connect(on_quit)
    _logger.info("Quant Hub v%s (Qt) ready — IBKR disconnected", __version__)
    return app, window


def main_qt() -> None:
    app, window = create_qt_app()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main_qt()
