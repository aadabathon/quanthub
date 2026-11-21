"""Daily option-chain snapshots for a watchlist (D63, D59).

D59 chose to accumulate chains automatically: each trading day, the first
time the app runs with TWS connected, it snapshots a watchlist you set
(``[collect] chains`` in settings). ``ChainService`` saves every live
fetch already (D33), so a run is that fetch with a wider request, once per
symbol per day.

**When a symbol is due:**
* today is an NYSE trading day (``data/trading_calendar.py``), and
* it's at least ``SETTLED`` (09:45 New York): before the open, option
  quotes are the previous session's, and the first quarter hour is noisy,
  and
* no snapshot of that symbol from that source is stamped today, New York
  date.

So a second connection the same day collects nothing, and a day you don't
open the app is simply missing. Nothing is backfilled.

**The request** is VARIANCE I3's wide band (decision 2):
``VARIANCE_REQUEST``, −6 to +3 ATM standard deviations with up to 41
strikes per expiry, so most of the variance is measured rather than
extrapolated. The fetch time is logged, so the first real runs measure
it.

A failed symbol is logged and the rest still run. Runs on the task-worker
thread; ``run`` blocks for each fetch.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from quant_hub.data.options import ChainRequest
from quant_hub.data.trading_calendar import is_trading_day
from quant_hub.services.option_chains import ChainService
from quant_hub.storage.chain_store import ChainStore

_logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
SETTLED = clock_time(9, 45)
VARIANCE_REQUEST = ChainRequest(k_range_sd=(-6.0, 3.0), strikes_per_expiry=41)


@dataclass(frozen=True)
class CollectOutcome:
    symbol: str
    ok: bool
    snapshot: int | None = None
    quotes: int = 0
    seconds: float = 0.0
    message: str = ""

    def __str__(self) -> str:
        if self.ok:
            return (f"{self.symbol}: snapshot #{self.snapshot}, {self.quotes} quotes "
                    f"in {self.seconds:.0f} s")
        return f"{self.symbol}: failed, {self.message}"


class ChainCollector:
    def __init__(self, chains: ChainService, store: ChainStore, watchlist: Sequence[str],
                 source: str = "ibkr",
                 now: Callable[[], datetime] = lambda: datetime.now(NEW_YORK),
                 timer: Callable[[], float] = time.monotonic) -> None:
        self._chains, self._store = chains, store
        self._watchlist = tuple(dict.fromkeys(s.strip().upper() for s in watchlist if s.strip()))
        self._source = source
        self._now, self._timer = now, timer

    @property
    def watchlist(self) -> tuple[str, ...]:
        return self._watchlist

    def due(self) -> list[str]:
        """The watchlist symbols to collect now (module docstring)."""
        if not self._watchlist or self._source not in self._chains.source_names:
            return []
        now = self._now().astimezone(NEW_YORK)
        if not is_trading_day(now.date()) or now.time() < SETTLED:
            return []
        return [s for s in self._watchlist if not self._collected(s, now.date())]

    def collect(self, symbol: str) -> CollectOutcome:
        """Snapshot one symbol with the wide request, and log the outcome. The
        GUI queues one symbol per worker task, so other work runs in between."""
        start = self._timer()
        try:
            chain, snapshot = self._chains.get_chain(symbol, self._source, VARIANCE_REQUEST)
            outcome = CollectOutcome(symbol, True, snapshot, len(chain.quotes),
                                     self._timer() - start)
        except Exception as exc:                  # one symbol's failure never stops the rest
            outcome = CollectOutcome(symbol, False, seconds=self._timer() - start,
                                     message=str(exc))
        (_logger.info if outcome.ok else _logger.warning)("daily chains: %s", outcome)
        return outcome

    def run(self) -> list[CollectOutcome]:
        """Every due symbol in turn (scripts and tests; the GUI queues them)."""
        return [self.collect(symbol) for symbol in self.due()]

    def _collected(self, symbol: str, today) -> bool:
        # Snapshots are stamped in local time without a zone (the chain store's
        # convention); read as local, then compared on the New York date.
        return any(s.source == self._source and s.as_of.astimezone(NEW_YORK).date() == today
                   for s in self._store.snapshots(symbol))
