"""The GUI-independent half of the composition root.

``build_core`` constructs and wires everything below the GUI — event bus,
logging, broker, stores, market service, task runner. ``app.py`` calls it
and builds the window on top; a script or notebook can call it alone to get
the same fully wired services without any widgets.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from quant_hub.broker.ibkr import IBKRBroker
from quant_hub.broker.market_source import IBKRSource
from quant_hub.broker.option_chain import IBKROptionChainSource
from quant_hub.config import AppConfig, load_config
from quant_hub.data.sources import SyntheticSource
from quant_hub.events import EventBus, EventBusLogHandler
from quant_hub.tasks import TaskRunner
from quant_hub.services.cached_source import CachedSource
from quant_hub.services.file_import import FileImportService, FileSource
from quant_hub.services.market_service import MarketService
from quant_hub.services.option_chains import ChainService
from quant_hub.services.portfolio import PortfolioService
from quant_hub.services.workstation import WorkstationService
from quant_hub.storage.bar_store import BarStore
from quant_hub.storage.chain_store import ChainStore
from quant_hub.storage.position_store import PositionStore
from quant_hub.storage.schema import prepare_store
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Core:
    config: AppConfig
    bus: EventBus
    broker: IBKRBroker
    store: BarStore
    state_store: ViewStateStore
    market_service: MarketService
    chain_store: ChainStore
    chains: ChainService
    workstation: WorkstationService
    file_import: FileImportService
    position_store: PositionStore
    portfolio: PortfolioService
    tasks: TaskRunner

    def shutdown(self) -> None:
        """Stop the worker, drop the broker connection, close the stores.
        Each step is guarded so one failure cannot skip the rest."""
        self.tasks.shutdown()
        for name, step in (
            ("disconnecting broker", self.broker.disconnect),
            ("closing bar store", self.store.close),
            ("closing view-state store", self.state_store.close),
            ("closing chain store", self.chain_store.close),
            ("closing position store", self.position_store.close),
        ):
            try:
                step()
            except Exception:
                _logger.exception("error %s at shutdown", name)


def setup_logging(bus: EventBus) -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Idempotent: building the app again in one process (smoke tests,
    # scripts) replaces our handlers instead of stacking duplicates, which
    # would print every record N times and keep dead buses alive.
    for handler in [h for h in root_logger.handlers if getattr(h, "quant_hub", False)]:
        root_logger.removeHandler(handler)

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
        console.quant_hub = True
        root_logger.addHandler(console)

    bus_handler = EventBusLogHandler(bus)
    bus_handler.setFormatter(logging.Formatter("%(message)s"))
    bus_handler.quant_hub = True
    root_logger.addHandler(bus_handler)

    # ibapi's own loggers narrate every request/response at INFO; that
    # belongs in a wire trace, not the GUI log panel (observed against live
    # TWS, 2026-08-14). Our broker module logs the meaningful events itself.
    logging.getLogger("ibapi").setLevel(logging.WARNING)


def build_core(config: AppConfig | None = None) -> Core:
    """Wire the core. With no ``config``, the user's settings file is read
    over the defaults (``config.load_config``); scripts and tests pass one."""
    problems: list[str] = []
    if config is None:
        config, problems = load_config()
    bus = EventBus()
    setup_logging(bus)
    for problem in problems:
        _logger.warning("settings: %s", problem)

    # Before any store opens the file: bring its schema up to date, backing
    # it up first if anything changes (storage/schema.py, D36).
    schema = prepare_store(config.db_path)
    if schema.migrated_from is not None:
        _logger.info("store schema v%d -> v%d; backup at %s", schema.migrated_from,
                     schema.version, schema.backup)

    broker = IBKRBroker(config, bus)
    store = BarStore(config.db_path)
    # Same db file as the bar store — just a second, unrelated table. Each
    # view owns its own state shape as an opaque JSON blob.
    state_store = ViewStateStore(config.db_path)
    # IBKR is wrapped in the cache; synthetic stays direct (generated
    # experiments are reproducible from their parameters — persistence is
    # for acquired observations only). No active source: every caller names
    # its source explicitly (D23).
    # Imported files are always a source (D34): "file" serves what was
    # imported. IBKR is optional: turned off in settings, it registers no
    # sources, and pages offer only the rest.
    ibkr = config.ibkr_enabled
    market_service = MarketService(
        sources=[SyntheticSource(), FileSource(store),
                 *([CachedSource(IBKRSource(broker), store)] if ibkr else [])],
    )
    # Option chains: live providers by name (D34), every fetch saved as a
    # snapshot in the same database (D33), plus "stored" for saved ones.
    chain_store = ChainStore(config.db_path)
    chains = ChainService([IBKROptionChainSource(broker)] if ibkr else [], chain_store)
    # Imported positions, kept as dated snapshots in the same database (P2).
    position_store = PositionStore(config.db_path)
    return Core(
        config=config, bus=bus, broker=broker, store=store, state_store=state_store,
        market_service=market_service, chain_store=chain_store, chains=chains,
        workstation=WorkstationService(store), file_import=FileImportService(store),
        position_store=position_store,
        portfolio=PortfolioService(market_service, position_store), tasks=TaskRunner(),
    )
