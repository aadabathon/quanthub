"""Company fundamentals for the DCF, from the SEC, kept in the store (W5).

``FundamentalsService.history`` turns a ticker or CIK into a
``FinancialHistory``:

1. **Resolve.** A CIK is used as given; a ticker goes through the SEC's
   ticker map, kept in the store and refreshed when a week old (new
   listings), or when a ticker isn't in it.
2. **Facts.** The filer's stored facts serve when they're under a day old;
   otherwise (or on ``refresh``) companyfacts is fetched, flattened to the
   concepts the mapping reads, and stored in place of the old ones. Filings
   arrive a few times a quarter, so a day-old copy can only have missed a
   filing made since; Refresh fetches at once. A failed fetch serves the
   stored copy when there is one, and says so in ``sources``.
3. **Map.** ``providers.sec_edgar.history_from_facts``: offline, so a
   mapping fix applies to stored facts without refetching.

The SEC identity (``[sec] identity`` in settings) is needed only to fetch:
with stored facts the page works offline without one. Runs on the
task-worker thread (network and database).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from quant_hub.data.fundamentals import FinancialHistory
from quant_hub.providers.sec_edgar import (
    SecClient,
    SecError,
    facts_from_companyfacts,
    history_from_facts,
    parse_identifier,
)
from quant_hub.storage.fundamentals_store import FundamentalsStore

_logger = logging.getLogger(__name__)

FACTS_STALE_AFTER = timedelta(days=1)       # module docstring: filings arrive quarterly
TICKERS_STALE_AFTER = timedelta(days=7)     # new listings
YEARS = 5                                   # fiscal years mapped, the original's default
NO_IDENTITY = ('set [sec] identity = "Your Name you@example.com" in settings.toml, then '
               "restart: the SEC asks programs that download its filings to say who is asking")


class FundamentalsService:
    def __init__(self, store: FundamentalsStore, identity: str,
                 client_factory: Callable[[str], SecClient] = SecClient,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self._store = store
        self._identity = identity.strip()
        self._client_factory = client_factory
        self._client: SecClient | None = None
        self._now = now

    @property
    def has_identity(self) -> bool:
        return bool(self._identity)

    def history(self, identifier: str, refresh: bool = False,
                years: int = YEARS) -> FinancialHistory:
        """The module docstring's three steps. Raises ``SecError`` (network,
        identity) or ``ValueError`` (an identifier that names nothing)."""
        cik, ticker = self._resolve(identifier)
        filer = self._store.filer(cik)
        stale = filer is None or self._now() - filer[1] > FACTS_STALE_AFTER
        note = ""
        if refresh or stale:
            try:
                doc = self._sec().companyfacts(cik)
                facts = facts_from_companyfacts(doc)
                name = str(doc.get("entityName") or (filer[0] if filer else f"CIK {cik}"))
                kept = self._store.save_facts(cik, name, facts, self._now())
                _logger.info("SEC facts for %s (CIK %d): %d kept", name, cik, kept)
                filer = self._store.filer(cik)
            except SecError as exc:
                if filer is None:
                    raise
                note = f"refresh failed ({exc}); serving the copy fetched {filer[1]:%Y-%m-%d %H:%M}"
                _logger.warning("fundamentals: %s", note)
        name, fetched_at = filer
        hist = history_from_facts(self._store.facts(cik), years, cik=cik, company=name,
                                  ticker=ticker or self._store.ticker_for(cik))
        hist.note_source("data", f"SEC companyfacts, fetched {fetched_at:%Y-%m-%d %H:%M}"
                         + (f"; {note}" if note else ""))
        return hist

    def _resolve(self, identifier: str) -> tuple[int, str | None]:
        parsed = parse_identifier(identifier)
        if isinstance(parsed, int):
            return parsed, None
        fetched = self._store.tickers_fetched_at()
        found = self._store.ticker(parsed)
        if found is None or fetched is None or self._now() - fetched > TICKERS_STALE_AFTER:
            try:
                self._store.save_tickers(self._sec().tickers(), self._now())
                found = self._store.ticker(parsed)
            except SecError:
                if found is None:
                    raise
        if found is None:
            raise ValueError(f"no SEC filer has the ticker {parsed!r}: try its CIK")
        return found[0], parsed

    def _sec(self) -> SecClient:
        if not self._identity:
            raise SecError(NO_IDENTITY)
        if self._client is None:
            self._client = self._client_factory(self._identity)
        return self._client
