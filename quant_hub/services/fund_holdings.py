"""Funds' holdings from N-PORT, kept in the store (D62).

``FundHoldingsService.holdings`` turns a fund's ticker into its latest
``FundHoldings``:

1. **Resolve.** The SEC's fund ticker map, kept in the store and refreshed
   when a week old, gives a series trust's CIK and series id. A ticker that
   isn't there (a unit trust, SPY) falls back to the company ticker map
   (one fetch per session) for its CIK.
2. **Check for a newer filing** when the stored report was checked more
   than ``STALE_AFTER`` ago, or on ``refresh``. Public reports arrive
   quarterly, so a monthly check misses nothing for long. The feed is one
   request. If its newest filing is already stored, the stored report is
   only marked as checked; otherwise the document is fetched and stored
   beside the older ones.
3. **Serve** the newest stored report. A failed check serves the stored
   copy and logs why.

Fetching needs ``[sec] identity`` in settings (D47), and stored reports
work offline without one. Runs on the task-worker thread.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from quant_hub.data.holdings import FundHoldings
from quant_hub.providers.sec_edgar import SecClient, SecError
from quant_hub.providers.sec_nport import (
    FUND_TICKERS_URL,
    NportError,
    document_url,
    feed_url,
    parse_filing_feed,
    parse_fund_tickers,
    parse_nport,
)
from quant_hub.services.fundamentals import NO_IDENTITY
from quant_hub.storage.holdings_store import HoldingsStore

_logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(days=30)            # public reports arrive quarterly
TICKERS_STALE_AFTER = timedelta(days=7)     # new funds


class FundHoldingsService:
    def __init__(self, store: HoldingsStore, identity: str,
                 client_factory: Callable[[str], SecClient] = SecClient,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self._store = store
        self._identity = identity.strip()
        self._client_factory = client_factory
        self._client: SecClient | None = None
        self._companies: dict[str, tuple[int, str]] | None = None
        self._now = now

    def holdings(self, fund: str, refresh: bool = False) -> FundHoldings:
        """The module docstring's three steps. Raises ``SecError`` (network,
        identity), ``NportError`` (an unreadable filing) or ``LookupError``
        (a ticker the SEC doesn't know as a fund or a company)."""
        fund = fund.strip().upper()
        if not fund:
            raise LookupError("enter a fund's ticker")
        stored = self._store.latest(fund)
        if refresh or stored is None or self._now() - stored[1] > STALE_AFTER:
            try:
                self._check(fund, stored)
            except (SecError, NportError) as exc:
                if stored is None:
                    raise
                _logger.warning("%s holdings: check failed (%s); serving the report of %s",
                                fund, exc, stored[0].report_date)
            stored = self._store.latest(fund)
        report = stored[0]
        _logger.info("%s holdings as of %s: %d lines, %.1f%% of net assets",
                     fund, report.report_date, len(report.holdings), 100 * report.weight_covered)
        return report

    def report_dates(self, fund: str) -> list[str]:
        return self._store.report_dates(fund)

    def _check(self, fund: str, stored: tuple[FundHoldings, datetime] | None) -> None:
        cik, series = self._resolve(fund)
        refs = parse_filing_feed(self._sec().get(feed_url(series or cik)))
        if not refs:
            raise NportError(f"EDGAR lists no NPORT-P filings for {fund}")
        latest = refs[0]
        known = self._store.accession(fund, latest.accession)
        if known is not None:
            self._store.touch(fund, known, self._now())
            return
        payload = self._sec().get(document_url(cik, latest.accession))
        report = parse_nport(payload, fund, latest.accession, latest.filed, series)
        kept = self._store.save(report, self._now(), hashlib.sha256(payload).hexdigest())
        _logger.info("%s: N-PORT %s as of %s, %d holdings", fund, latest.accession,
                     report.report_date, kept)

    def _resolve(self, fund: str) -> tuple[int, str]:
        fetched = self._store.fund_tickers_fetched_at()
        found = self._store.fund_ticker(fund)
        if found is None or fetched is None or self._now() - fetched > TICKERS_STALE_AFTER:
            try:
                self._store.save_fund_tickers(parse_fund_tickers(self._sec().get(FUND_TICKERS_URL)),
                                              self._now())
                found = self._store.fund_ticker(fund)
            except (SecError, NportError):
                if found is None and fetched is None:
                    raise
        if found is not None:
            return found[0], found[1]
        if self._companies is None:                       # unit trusts: no series id
            self._companies = self._sec().tickers()
        company = self._companies.get(fund)
        if company is None:
            raise LookupError(f"the SEC knows no fund or company with the ticker {fund!r}")
        return company[0], ""

    def _sec(self) -> SecClient:
        if not self._identity:
            raise SecError(NO_IDENTITY)
        if self._client is None:
            self._client = self._client_factory(self._identity)
        return self._client
