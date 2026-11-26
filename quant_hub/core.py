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
from pathlib import Path

from quant_hub.broker.ibkr import IBKRBroker
from quant_hub.broker.market_source import IBKRSource
from quant_hub.broker.option_chain import IBKROptionChainSource
from quant_hub.config import AppConfig, load_config
from quant_hub.data.sources import SyntheticSource
from quant_hub.events import EventBus, EventBusLogHandler
from quant_hub.providers.massive import MassiveClient
from quant_hub.tasks import TaskRunner
from quant_hub.services.accounts import AccountService
from quant_hub.services.activity import ActivityService
from quant_hub.services.book import PortfolioBook
from quant_hub.services.desk import VOICES, DeskService, tail_voice
from quant_hub.services.cached_source import CachedSource
from quant_hub.services.chain_collector import ChainCollector
from quant_hub.services.external_data import ExternalDataService
from quant_hub.services.file_import import FileImportService, FileSource
from quant_hub.services.fund_holdings import FundHoldingsService
from quant_hub.services.hedge import HedgeService
from quant_hub.services.fundamentals import FundamentalsService
from quant_hub.services.market_service import MarketService
from quant_hub.services.massive_source import MassiveSource
from quant_hub.services.option_chains import ChainService
from quant_hub.services.portfolio import PortfolioService
from quant_hub.services.research import ResearchService
from quant_hub.services.security_ids import SecurityIdService
from quant_hub.services.valuation import ValuationService
from quant_hub.services.workstation import WorkstationService
from quant_hub.storage.activity_store import ActivityStore
from quant_hub.storage.bar_store import BarStore
from quant_hub.storage.chain_store import ChainStore
from quant_hub.storage.desk_store import DeskStore
from quant_hub.storage.fundamentals_store import FundamentalsStore
from quant_hub.storage.holdings_store import HoldingsStore
from quant_hub.storage.position_store import PositionStore
from quant_hub.storage.research_store import ResearchStore
from quant_hub.storage.schema import prepare_store
from quant_hub.storage.series_store import SeriesStore
from quant_hub.storage.state_store import ViewStateStore
from quant_hub.storage.valuation_store import ValuationStore

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
    chain_collector: ChainCollector
    workstation: WorkstationService
    file_import: FileImportService
    position_store: PositionStore
    portfolio: PortfolioService
    research_store: ResearchStore
    research: ResearchService
    fundamentals_store: FundamentalsStore
    fundamentals: FundamentalsService
    valuation_store: ValuationStore
    valuation: ValuationService
    series_store: SeriesStore
    external_data: ExternalDataService
    holdings_store: HoldingsStore
    fund_holdings: FundHoldingsService
    security_ids: SecurityIdService
    hedge: HedgeService
    # Today's book as Portfolio analyses it: What-if's and the Hedge Lab's,
    # unless an extension supplies its own (D89).
    book: PortfolioBook
    # A broker's transaction history, imported (TR1, D90).
    activity_store: ActivityStore
    activity: ActivityService
    # The desk: snapshots of every voice's claims, and decisions (D1, D96).
    desk_store: DeskStore
    desk: DeskService
    tasks: TaskRunner
    # Read-only IBKR accounts (E1); None when IBKR is off in settings.
    accounts: AccountService | None = None

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
            ("closing research store", self.research_store.close),
            ("closing fundamentals store", self.fundamentals_store.close),
            ("closing valuation store", self.valuation_store.close),
            ("closing series store", self.series_store.close),
            ("closing holdings store", self.holdings_store.close),
            ("closing activity store", self.activity_store.close),
            ("closing desk store", self.desk_store.close),
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
    # Massive's daily bars, cached the same way, when [massive] key is set (D97).
    massive = ([CachedSource(MassiveSource(MassiveClient(config.massive_key)), store)]
               if config.massive_key else [])
    market_service = MarketService(
        sources=[SyntheticSource(), FileSource(store),
                 *([CachedSource(IBKRSource(broker), store)] if ibkr else []), *massive],
    )
    # Option chains: live providers by name (D34), every fetch saved as a
    # snapshot in the same database (D33), plus "stored" for saved ones.
    chain_store = ChainStore(config.db_path)
    chains = ChainService([IBKROptionChainSource(broker)] if ibkr else [], chain_store)
    # Imported positions, kept as dated snapshots in the same database (P2).
    position_store = PositionStore(config.db_path)
    research_store = ResearchStore(config.db_path)
    # SEC facts as filed, same database (W5); fetching needs [sec] identity.
    fundamentals_store = FundamentalsStore(config.db_path)
    fundamentals = FundamentalsService(fundamentals_store, config.sec_identity)
    # Saved valuations, immutable records in the same database (W6).
    valuation_store = ValuationStore(config.db_path)
    valuation = ValuationService(fundamentals, market_service, valuation_store)
    portfolio = PortfolioService(market_service, position_store)
    # External series as fetched (Ken French first), same database (D60).
    series_store = SeriesStore(config.db_path)
    # Funds' N-PORT holdings, same database (D62); fetching needs [sec] identity.
    holdings_store = HoldingsStore(config.db_path)
    fund_holdings = FundHoldingsService(holdings_store, config.sec_identity)
    # Held symbols' ISINs from IBKR, kept in the same database (A1, D66).
    security_ids = SecurityIdService(holdings_store, broker if ibkr else None)
    external_data = ExternalDataService(series_store, fred_key=config.fred_key)
    research = ResearchService(market_service, research_store)
    # A broker's transaction history, same database (TR1, D90).
    activity_store = ActivityStore(config.db_path)
    activity = ActivityService(activity_store, position_store)
    # The desk's snapshots and decisions, same database; JSON beside it (D96).
    desk_store = DeskStore(config.db_path)
    desk = DeskService(desk_store, market_service, activity,
                       Path(config.db_path).parent / "desk",
                       voices=(*VOICES, tail_voice(external_data)), external=external_data)
    return Core(
        config=config, bus=bus, broker=broker, store=store, state_store=state_store,
        market_service=market_service, chain_store=chain_store, chains=chains,
        # The watchlist's chains, once a trading day after the open (D63).
        chain_collector=ChainCollector(chains, chain_store, config.collect_chains),
        workstation=WorkstationService(store), file_import=FileImportService(store),
        position_store=position_store, portfolio=portfolio, tasks=TaskRunner(),
        research_store=research_store, research=research,
        fundamentals_store=fundamentals_store, fundamentals=fundamentals,
        valuation_store=valuation_store, valuation=valuation,
        series_store=series_store, external_data=external_data,
        holdings_store=holdings_store, fund_holdings=fund_holdings, security_ids=security_ids,
        # The hedge lab's job, on stored data only (HEDGE U3, D70).
        hedge=HedgeService(external_data, chains, store),
        book=PortfolioBook(portfolio, market_service, state_store),
        activity_store=activity_store, activity=activity, desk_store=desk_store, desk=desk,
        accounts=AccountService(broker) if ibkr else None,
    )
