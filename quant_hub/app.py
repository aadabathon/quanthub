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

from PySide6.QtWidgets import QApplication

import quant_hub.gui  # noqa: F401  (pins qtpy to PySide6 before qtawesome loads)
from quant_hub import __version__
from quant_hub.config import AppConfig
from quant_hub.core import Core, build_core
from quant_hub.extensions import (
    extensions_wanted,
    load_extensions,
    merge_book,
    merge_commands,
    merge_pages,
    shut_down,
)
from quant_hub.gui.app_icon import APP_USER_MODEL_ID, app_icon
from quant_hub.gui.main_window import MainWindow, PageSections
from quant_hub.gui.pages.dashboard import DashboardPage
from quant_hub.gui.pages.data import DataPage
from quant_hub.gui.pages.desk import DeskPage
from quant_hub.gui.pages.garch_lab import GARCHLabPage
from quant_hub.gui.pages.hedge_lab import HedgeLabPage
from quant_hub.gui.pages.kalman_lab import KalmanLabPage
from quant_hub.gui.pages.markets import MarketsPage
from quant_hub.gui.pages.markov_lab import MarkovLabPage
from quant_hub.gui.pages.options_lab import OptionsLabPage
from quant_hub.gui.pages.portfolio import PortfolioPage
from quant_hub.gui.pages.research import ResearchPage
from quant_hub.gui.pages.returns import ReturnHistogramPage
from quant_hub.gui.pages.simulator import SimulatorPage
from quant_hub.gui.pages.trades import TradesPage
from quant_hub.gui.pages.valuation import ValuationPage
from quant_hub.gui.pages.volatility_lab import VolatilityLabPage
from quant_hub.gui.pages.whatif import WhatIfPage
from quant_hub.gui.theme import apply_theme
from quant_hub.services.book import BookSource, Grader
from quant_hub.services.desk import default_source
from quant_hub.services.whatif import WhatIfService

_logger = logging.getLogger(__name__)

def _sections(core: Core, book: BookSource | None = None,
              grader: Grader | None = None) -> PageSections:
    """Quant Hub's pages. ``book`` and ``grader`` are an extension's, when
    one supplies them (D89); What-if and the Hedge Lab read ``core.book``
    otherwise, and What-if grades nothing."""
    book = book if book is not None else core.book
    dashboard = ("Dashboard", "ph.house", lambda shell: DashboardPage(
        shell, core.workstation, core.file_import, core.tasks, core.state_store, core.config))
    desk = ("Desk", "ph.users-three", lambda _shell: DeskPage(
        core.desk, [s for s in core.market_service.source_names if s != "synthetic"],
        default_source(bool(core.config.massive_key), core.config.ibkr_enabled),
        core.tasks, core.state_store))
    markets = ("Markets", "ph.chart-bar", lambda shell: MarketsPage(
        shell, core.market_service, core.workstation, core.tasks, core.state_store))
    returns = ("Return histogram", "mdi6.chart-histogram", lambda _shell: ReturnHistogramPage(
        core.workstation, core.tasks, core.state_store))
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
        core.portfolio, core.market_service, core.tasks, core.state_store, core.accounts,
        core.activity))
    trades = ("Trades", "ph.receipt", lambda _shell: TradesPage(
        core.activity, core.market_service, core.tasks, core.state_store))
    valuation = ("Valuation", "ph.scales", lambda _shell: ValuationPage(
        core.valuation, core.portfolio, core.tasks, core.state_store))
    data = ("Data", "ph.database", lambda shell: DataPage(
        shell, core.external_data, core.fund_holdings, core.chains, core.chain_collector,
        core.workstation, core.tasks, core.state_store))
    whatif = ("What-if", "ph.git-diff", lambda _shell: WhatIfPage(
        WhatIfService(core.portfolio, book, core.state_store, core.external_data, grader),
        core.tasks, core.state_store))
    hedge_lab = ("Hedge Lab", "ph.umbrella", lambda _shell: HedgeLabPage(
        core.hedge, book, core.tasks, core.state_store))
    research = ("Research", "ph.file-text", lambda _shell: ResearchPage(
        core.research, core.market_service, core.tasks, core.state_store))
    return [
        (None, [dashboard, desk, markets, returns, data, portfolio, trades, whatif, hedge_lab,
                valuation, simulator, options]),
        ("Research", [garch, vol_lab, kalman, markov, research]),
    ]


#: Terminal mnemonics Ctrl+K also finds a page by (Godel's command names).
PAGE_ALIASES = {"HRH": "Return histogram"}


def create_app(config: AppConfig | None = None,
               argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    """Build and wire the application without entering the event loop
    (smoke tests and screenshot scripts drive it from here)."""
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Quant Hub")
    app.setWindowIcon(app_icon())
    apply_theme(app)
    core = build_core(config)
    # Installed extensions add their pages and commands, and may supply
    # today's book and a grader (D86, D87, D89). One that fails is logged and
    # skipped: an extension never stops the app.
    extensions, problems = (load_extensions(core) if extensions_wanted(core.config)
                            else ([], []))
    book, grader, refused = merge_book(extensions)
    sections, placement = merge_pages(_sections(core, book, grader), extensions)
    commands, taken = merge_commands(extensions)
    for problem in [*problems, *refused, *placement, *taken]:
        _logger.warning("extension %s", problem)
    for extension in extensions:
        _logger.info("extension %s: %d page(s), %d command(s)", extension.name,
                     len(extension.pages), len(extension.commands))

    window = MainWindow(core, sections, PAGE_ALIASES, commands)
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
        # After the worker has stopped, so no job is using what they close.
        shut_down(extensions)

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
