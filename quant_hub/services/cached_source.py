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

Daily bars only: the coverage rule above (complete through yesterday) is
right for daily bars and nothing else, so any other ``bar_size`` is
rejected at construction, loudly, instead of recording wrong coverage
(DATA_SYSTEM_IMPROVEMENTS #7). Weekly and intraday bars need their own
coverage rules first.

Two price bases (D38)
---------------------
Traded bars (IBKR's TRADES) are cached as above. Adjusted bars (its
ADJUSTED_LAST) can't be: every ex-dividend date rescales *all* earlier
adjusted prices by its factor :math:`f = 1 - D/C_{prev}`, so bars fetched
on different days are on different scales, and stitching them together
would put a fake jump at the seam. So adjusted bars are kept whole:

* **Once a day:** the first adjusted request of a calendar day refetches
  the whole stored span (one request, under a second) and replaces it.
  Later requests that day fetch only what the traded sync fetched: the
  same day, the same scale.
* **The split guard.** Traded prices rewrite history too, at each split,
  so a cached traded series can straddle one: old bars at the pre-split
  scale, new ones after. The daily refetch checks for this. Over completed
  sessions, the ratio :math:`r_t = A_t / P_t` of today's adjusted prices to
  the stored traded ones must be a dividend step function: at most 1, and
  rising from one day to the next by at most ``MAX_DIVIDEND_STEP`` (20%: a
  dividend :math:`D` steps it by :math:`D/(C - D)`), never falling. "At
  most" and "never" allow a cent's rounding at each day's price
  (``PRICE_TICK``), which a split (a factor of 2 or 10) dwarfs.
  A stored traded series that fails was written before a split or other
  corporate action, so it is refetched whole and replaced. A false alarm
  (a special dividend over 20%) only costs that refetch.

Synthetic data deliberately does **not** flow through here: generated
experiments are reproducible from their parameters; persistence is for
externally acquired observations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

import numpy as np
import pandas as pd

from quant_hub.data.bars import validate_ohlcv_frame
from quant_hub.data.ranges import ONE_DAY, DateRange, floor_day
from quant_hub.data.sources import BarSource
from quant_hub.data.types import PriceBasis, PriceHistory, Quote
from quant_hub.services.sync import SyncStatus, plan_sync
from quant_hub.storage.bar_store import BarStore, InstrumentKey

_logger = logging.getLogger(__name__)

DAILY = "1 day"

MAX_DIVIDEND_STEP = 0.20
"""Largest one-day rise in adjusted/traded taken as a dividend (split guard)."""

RATIO_TOLERANCE = 1e-4
"""Slack in the split guard's checks for numerical noise: IBKR's ratio holds
to ~1e-7 between ex-dates."""

PRICE_TICK = 0.01
"""Prices are rounded to the cent, which moves adjusted/traded by up to
``PRICE_TICK / price`` a day: 0.04% at a split-adjusted $13. The guard
allows that on top of ``RATIO_TOLERANCE`` (found live, 2026-10-22: a
stock's split-adjusted early history, refetched two months apart, differed
by up to 0.04%, and a fixed 0.01% tolerance called it a corporate action)."""


class CachedSource:
    """See module docstring. Implements the ``DataSource`` protocol."""

    def __init__(
        self,
        delegate: BarSource,
        store: BarStore,
        bar_size: str = DAILY,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        if bar_size != DAILY:
            raise ValueError(f"CachedSource stores daily bars only, not {bar_size!r}: its "
                             "coverage runs through yesterday, which is right only for "
                             "daily bars (module docstring)")
        self.name = delegate.name  # transparent: registers under the provider's name
        self._delegate = delegate
        self._store = store
        self._bar_size = bar_size
        self._now = now  # injectable for deterministic tests

    # -------------------------------------------------------------- DataSource
    def get_history(self, symbol: str, start: datetime, end: datetime,
                    basis: PriceBasis = PriceBasis.TRADED) -> PriceHistory:
        symbol = symbol.upper().strip()
        key = InstrumentKey(symbol=symbol)
        requested = DateRange(start, end)
        _logger.info(
            "history request: %s %s (%s %s bars, source %s)",
            symbol, requested, basis.value, self._bar_size, self.name,
        )

        covered = self._store.coverage(key, self.name, self._bar_size)
        plan = plan_sync(requested, covered)
        _logger.info("cache decision for %s: %s", symbol, plan.describe())

        if plan.status is not SyncStatus.FULL_HIT:
            self._sync_missing(key, plan.missing)
        # An adjusted series refreshed today follows every traded sync, so it
        # always spans the traded coverage; otherwise only an adjusted
        # request refreshes it.
        if basis is PriceBasis.ADJUSTED or self._adjusted_current(key):
            self._sync_adjusted(key, plan.missing)

        frame = self._store.query_bars(key, self.name, self._bar_size, start, end, basis)
        if len(frame) == 0:
            raise ValueError(
                f"no {self._bar_size} bars available for {symbol} in {requested}"
            )
        _logger.info("serving %s: %d %s bars from local store", symbol, len(frame),
                     basis.value)
        # Columns the provider never supplied are stored as NULL; drop them
        # rather than hand analytics all-NaN columns.
        frame = frame.dropna(axis=1, how="all")
        return PriceHistory(symbol=symbol, source=self.name, frame=frame, basis=basis)

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

    def _adjusted_current(self, key: InstrumentKey) -> bool:
        refreshed = self._store.refreshed_on(key, self.name, self._bar_size,
                                             PriceBasis.ADJUSTED)
        return refreshed is not None and refreshed >= floor_day(self._now())

    def _sync_adjusted(self, key: InstrumentKey, missing: tuple[DateRange, ...]) -> None:
        """Keep the adjusted series whole and current (module docstring)."""
        today = floor_day(self._now())
        if self._adjusted_current(key):
            for gap in missing:                       # same day, same scale
                clean = self._fetch(key, gap, PriceBasis.ADJUSTED)
                self._store.insert_bars(key, self.name, self._bar_size, clean,
                                        PriceBasis.ADJUSTED)
            return
        covered = self._store.coverage(key, self.name, self._bar_size)
        if not covered:
            return
        span = DateRange(covered[0].start, max(covered[-1].end, today))
        adjusted = self._fetch(key, span, PriceBasis.ADJUSTED)
        traded = self._store.query_bars(key, self.name, self._bar_size, span.start,
                                        span.end, PriceBasis.TRADED)
        if not dividend_steps_only(traded, adjusted, today):
            _logger.warning(
                "%s: the stored traded prices don't match today's adjusted ones as "
                "dividends alone would (a split or other corporate action since they were "
                "stored); refetching the traded history %s", key.symbol, span)
            traded = self._fetch(key, span, PriceBasis.TRADED)
            self._store.replace_bars(key, self.name, self._bar_size, traded,
                                     PriceBasis.TRADED)
            if not dividend_steps_only(traded, adjusted, today):
                _logger.warning("%s: still not a dividend step function after the refetch "
                                "(a special dividend over %.0f%%?); keeping IBKR's series",
                                key.symbol, MAX_DIVIDEND_STEP * 100)
        result = self._store.replace_bars(key, self.name, self._bar_size, adjusted,
                                          PriceBasis.ADJUSTED)
        self._store.mark_refreshed(key, self.name, self._bar_size, PriceBasis.ADJUSTED, today)
        _logger.info("refreshed adjusted %s %s: %d bars", key.symbol, span,
                     result.inserted + result.replaced)

    def _fetch(self, key: InstrumentKey, window: DateRange, basis: PriceBasis) -> pd.DataFrame:
        raw = self._delegate.fetch_bars(key.symbol, window.start, window.end, self._bar_size,
                                        basis)
        clean, report = validate_ohlcv_frame(raw)
        log = _logger.info if report.clean else _logger.warning
        log("received %s %s %s: %s", basis.value, key.symbol, window, report.summary())
        return clean

    def _record_coverage(self, key: InstrumentKey, fetched: DateRange) -> DateRange | None:
        """Record ``fetched`` as covered, capped at yesterday (see module doc)."""
        yesterday = floor_day(self._now()) - ONE_DAY
        if fetched.start > yesterday:
            return None
        capped = DateRange(fetched.start, min(fetched.end, yesterday))
        self._store.add_coverage(key, self.name, self._bar_size, capped)
        return capped


def dividend_steps_only(traded: pd.DataFrame, adjusted: pd.DataFrame, today: datetime) -> bool:
    """Whether adjusted/traded over completed sessions is a dividend step
    function: at most 1, never falling, and rising by at most
    ``MAX_DIVIDEND_STEP`` a day (module docstring, the split guard).
    Today's bar is left out: the stored one is provisional."""
    common = traded.index.intersection(adjusted.index)
    common = common[common < today]
    if len(common) < 2:
        return True
    a = adjusted.loc[common, "close"].to_numpy(dtype=float)
    p = traded.loc[common, "close"].to_numpy(dtype=float)
    ratio = a / p
    # Each day's slack: noise, plus a cent's rounding at that day's price.
    slack = RATIO_TOLERANCE + PRICE_TICK / np.minimum(a, p)
    steps = ratio[1:] / ratio[:-1]
    return bool(np.all(ratio <= 1 + slack)
                and np.all(steps >= 1 - (slack[1:] + slack[:-1]))
                and np.all(steps <= 1 + MAX_DIVIDEND_STEP))
