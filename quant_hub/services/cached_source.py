"""``CachedSource``: a ``DataSource`` that persists what it fetches.

This is the seam the ROADMAP promised ("a CachedSource behind the same
DataSource protocol — this is why the protocol exists"). It decorates a
:class:`~quant_hub.data.sources.BarSource` (IBKR today, any provider later)
with a local SQLite store, so the rest of the application keeps calling
``MarketService.get_history`` and never learns whether bars came from disk
or the network.

Pipeline for one request (runs entirely on the task-worker thread)::

    coverage lookup ─► plan_sync ─► FULL_HIT: serve from store
                                 └► PARTIAL_HIT / MISS:
                                        fetch_bars(missing ranges)
                                        validate (data/bars.py)
                                        upsert into BarStore
                                        record coverage
                                        serve the union from the store

Failure policy: if any needed fetch fails, the whole request fails —
returning a silently incomplete series would poison downstream analytics.
The error notes when cached data exists so the user can narrow the window.

Same-day bars: a daily bar for *today* is provisional until the session
closes, so coverage is only ever recorded up to **yesterday**. A request
ending "now" therefore always re-fetches today's sliver (one bar), which
both refreshes the provisional bar (the store upserts) and keeps FULL_HIT
honest for every completed session.

Synthetic data deliberately does **not** flow through here: generated
experiments are reproducible from their parameters; persistence is for
externally acquired observations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from quant_hub.data.bars import validate_ohlcv_frame
from quant_hub.data.ranges import ONE_DAY, DateRange, floor_day
from quant_hub.data.sources import BarSource
from quant_hub.data.types import PriceHistory, Quote
from quant_hub.services.sync import SyncStatus, plan_sync
from quant_hub.storage.bar_store import BarStore, InstrumentKey

_logger = logging.getLogger(__name__)


class CachedSource:
    """See module docstring. Implements the ``DataSource`` protocol."""

    def __init__(
        self,
        delegate: BarSource,
        store: BarStore,
        bar_size: str = "1 day",
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.name = delegate.name  # transparent: registers under the provider's name
        self._delegate = delegate
        self._store = store
        self._bar_size = bar_size
        self._now = now  # injectable for deterministic tests

    # -------------------------------------------------------------- DataSource
    def get_history(self, symbol: str, start: datetime, end: datetime) -> PriceHistory:
        symbol = symbol.upper().strip()
        key = InstrumentKey(symbol=symbol)
        requested = DateRange(start, end)
        _logger.info(
            "history request: %s %s (%s bars, source %s)",
            symbol, requested, self._bar_size, self.name,
        )

        covered = self._store.coverage(key, self.name, self._bar_size)
        plan = plan_sync(requested, covered)
        _logger.info("cache decision for %s: %s", symbol, plan.describe())

        if plan.status is not SyncStatus.FULL_HIT:
            self._sync_missing(key, plan.missing)

        frame = self._store.query_bars(key, self.name, self._bar_size, start, end)
        if len(frame) == 0:
            raise ValueError(
                f"no {self._bar_size} bars available for {symbol} in {requested}"
            )
        _logger.info("serving %s: %d bars from local store", symbol, len(frame))
        # Columns the provider never supplied are stored as NULL; drop them
        # rather than hand analytics all-NaN columns.
        frame = frame.dropna(axis=1, how="all")
        return PriceHistory(symbol=symbol, source=self.name, frame=frame)

    def get_quote(self, symbol: str) -> Quote:
        """Quotes are moments, not history: always pass through, never cached."""
        return self._delegate.get_quote(symbol)

    # ------------------------------------------------------------------- sync
    def _sync_missing(self, key: InstrumentKey, missing: tuple[DateRange, ...]) -> None:
        for gap in missing:
            try:
                raw = self._delegate.fetch_bars(
                    key.symbol, gap.start, gap.end, self._bar_size
                )
            except Exception:
                _logger.error(
                    "fetch failed for %s %s; local store left unchanged for "
                    "this range (cached data outside it is unaffected)",
                    key.symbol, gap,
                )
                raise
            clean, report = validate_ohlcv_frame(raw)
            if report.clean:
                _logger.info("received %s %s: %s", key.symbol, gap, report.summary())
            else:
                _logger.warning("received %s %s: %s", key.symbol, gap, report.summary())

            result = self._store.insert_bars(key, self.name, self._bar_size, clean)
            covered_until = self._record_coverage(key, gap)
            _logger.info(
                "persisted %s: %d inserted, %d replaced; coverage recorded %s",
                key.symbol, result.inserted, result.replaced,
                covered_until if covered_until else "none (in-progress session)",
            )

    def _record_coverage(self, key: InstrumentKey, fetched: DateRange) -> DateRange | None:
        """Record ``fetched`` as covered, capped at yesterday (see module doc)."""
        yesterday = floor_day(self._now()) - ONE_DAY
        if fetched.start > yesterday:
            return None
        capped = DateRange(fetched.start, min(fetched.end, yesterday))
        self._store.add_coverage(key, self.name, self._bar_size, capped)
        return capped
