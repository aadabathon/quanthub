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
from quant_hub.gui.app_icon import APP_USER_MODEL_ID, app_icon
from quant_hub.gui.main_window import MainWindow, PageSections
from quant_hub.gui.pages.dashboard import DashboardPage
from quant_hub.gui.pages.garch_lab import GARCHLabPage
from quant_hub.gui.pages.kalman_lab import KalmanLabPage
from quant_hub.gui.pages.markets import MarketsPage
from quant_hub.gui.pages.markov_lab import MarkovLabPage
from quant_hub.gui.pages.options_lab import OptionsLabPage
from quant_hub.gui.pages.research import ResearchPage
from quant_hub.gui.pages.portfolio import PortfolioPage
from quant_hub.gui.pages.review import ReviewPage
from quant_hub.gui.pages.simulator import SimulatorPage
from quant_hub.gui.pages.valuation import ValuationPage
from quant_hub.gui.pages.volatility_lab import VolatilityLabPage
from quant_hub.gui.theme import apply_theme

_logger = logging.getLogger(__name__)

def _sections(core: Core) -> PageSections:
    dashboard = ("Dashboard", "ph.house", lambda shell: DashboardPage(
        shell, core.workstation, core.file_import, core.tasks, core.state_store, core.config))
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
    portfolio = ("Portfolio", "ph.chart-pie-slice", lambda _shell: PortfolioPage(
        core.portfolio, core.market_service, core.tasks, core.state_store, core.accounts))
    valuation = ("Valuation", "ph.scales", lambda _shell: ValuationPage(
        core.valuation, core.portfolio, core.tasks, core.state_store))
    review = ("Review", "ph.clipboard-text", lambda _shell: ReviewPage(core.review, core.tasks))
    research = ("Research", "ph.file-text", lambda _shell: ResearchPage(
        core.research, core.market_service, core.tasks, core.state_store))
    return [
        (None, [dashboard, review, markets, portfolio, valuation, simulator, options]),
        ("Research", [garch, vol_lab, kalman, markov, research]),
    ]


def create_app(config: AppConfig | None = None,
               argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    """Build and wire the application without entering the event loop
    (smoke tests and screenshot scripts drive it from here)."""
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Quant Hub")
    app.setWindowIcon(app_icon())
    apply_theme(app)
    core = build_core(config)

    window = MainWindow(core, _sections(core))
    # Qt recreates an already-visible native window when its first
    # WebEngine widget is attached. Prepare that page while still hidden
    # so the window uses the right graphics surface from its first frame.
    # This does not call on_show: no chain fetch or fit starts at launch.
    window.prepare_page("Volatility Lab")
    window.status.set_store_state("Local store ready", ok=True)
    # Only the height is fixed. The minimum width comes from the pages: each
    # asks for its content's width (base.ScrollPage), so none clips.
    window.setMinimumHeight(700)
    # Where the window was last time, or the default size on the main screen.
    if not window.restore_geometry():
        window.apply_default_size(app.primaryScreen())

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
    if sys.platform == "win32":
        # Before any window exists: see APP_USER_MODEL_ID.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    app, window = create_app()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
