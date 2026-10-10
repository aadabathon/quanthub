"""The toolkit-independent half of the composition root.

``build_core`` constructs and wires everything below the GUI — event bus,
logging, broker, stores, market service, task runner — exactly once. The two
GUI composition roots (``app.py`` for Tkinter, ``app_qt.py`` for Qt) each
call it and then build only their own widgets on top, so both front ends run
on an identical core during the Qt migration (D31).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from quant_hub.broker.ibkr import IBKRBroker
from quant_hub.broker.market_source import IBKRSource
from quant_hub.config import AppConfig
from quant_hub.data.sources import SyntheticSource
from quant_hub.events import EventBus, EventBusLogHandler
from quant_hub.gui.task_runner import TaskRunner
from quant_hub.services.cached_source import CachedSource
from quant_hub.services.market_service import MarketService
from quant_hub.services.workstation import WorkstationService
from quant_hub.storage.bar_store import BarStore
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
    workstation: WorkstationService
    tasks: TaskRunner

    def shutdown(self) -> None:
        """Stop the worker, drop the broker connection, close the stores.
        Each step is guarded so one failure cannot skip the rest."""
        self.tasks.shutdown()
        for name, step in (
            ("disconnecting broker", self.broker.disconnect),
            ("closing bar store", self.store.close),
            ("closing view-state store", self.state_store.close),
        ):
            try:
                step()
            except Exception:
                _logger.exception("error %s at shutdown", name)


def setup_logging(bus: EventBus) -> None:
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


def build_core(config: AppConfig | None = None) -> Core:
    config = config or AppConfig()
    bus = EventBus()
    setup_logging(bus)

    broker = IBKRBroker(config, bus)
    store = BarStore(config.db_path)
    # Same db file as the bar store — just a second, unrelated table. Each
    # view owns its own state shape as an opaque JSON blob.
    state_store = ViewStateStore(config.db_path)
    # IBKR is wrapped in the cache; synthetic stays direct (generated
    # experiments are reproducible from their parameters — persistence is
    # for acquired observations only). No active source: every caller names
    # its source explicitly (D23).
    market_service = MarketService(
        sources=[SyntheticSource(), CachedSource(IBKRSource(broker), store)],
    )
    return Core(
        config=config, bus=bus, broker=broker, store=store, state_store=state_store,
        market_service=market_service, workstation=WorkstationService(store),
        tasks=TaskRunner(),
    )
