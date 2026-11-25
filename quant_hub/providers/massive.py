"""Massive (formerly Polygon.io): daily stock bars and dividends (D97).

Terms (checked 2026-11-24, D97): the Individuals and Market Data Terms of
Service, both updated 2026-11-03.
- **Personal, non-commercial use**, with a license to "access, download,
  store and use" the data on the plan.
- **No redistribution,** and no use of the data to "train, fine-tune or
  distill" an AI or machine-learning model.
- **Stored copies are deleted within 30 days** if the license ends. Bars
  are kept by source, so this source's can be removed on their own.
- **A free API key** from massive.com, kept in ``settings.toml`` as
  ``[massive] key``. It's sent in the ``Authorization`` header, so it's
  never in a URL, a log line or an error message.
- **The free plan** gives end-of-day bars, two years of history, and about
  five requests a minute. The client waits ``MIN_INTERVAL_S`` between
  requests and never retries, so it can't run over the limit.

Two endpoints, as documented:
- **Daily bars:** ``/v2/aggs/ticker/{T}/range/1/day/{from}/{to}``, with
  ``adjusted=true``: adjusted for splits only, not dividends, which is what
  Quant Hub's traded basis is (IBKR's TRADES are split-adjusted too, D38). Each result has ``o h l c v``
  and ``t``, the Unix millisecond start of the session's window in New York.
  Midnight there is 04:00 or 05:00 UTC on the same date, so ``t``'s UTC
  date is the session's date, with no time-zone database needed. Pages
  continue through ``next_url``.
- **Dividends:** ``/stocks/v1/dividends?ticker=T``. Each has an
  ``ex_dividend_date`` and a ``split_adjusted_cash_amount``: the cash per
  share on today's split scale, the same scale as the bars.

This module only fetches and parses, into plain rows. Turning them into
bars on Quant Hub's two price bases is ``services/massive_source.py``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

HOST = "https://api.massive.com"
SOURCE = "massive"
USER_AGENT = "QuantHub/1.1 (personal research)"
TIMEOUT_S = 60.0
MIN_INTERVAL_S = 12.5            # five requests a minute, with room
MAX_PAGES = 20


class MassiveError(Exception):
    """A request was refused or its reply wasn't what the docs describe;
    the message says what to do and never carries the key. ``status`` is
    the HTTP status of a refusal, else None."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MassiveUnreachable(MassiveError):
    """No answer at all (no network, a timeout). Says nothing about the
    data, so the adapter treats it as offline."""


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Dividend:
    ex_date: date
    cash: float                  # per share, split-adjusted like the bars


def valid_key(key: str) -> bool:
    return bool(key) and key.strip() == key and " " not in key and len(key) >= 16


class MassiveClient:
    """Fetches bars and dividends (module docstring). ``opener``, ``sleep``
    and ``clock`` are injectable for tests."""

    def __init__(self, key: str, opener: Callable = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 min_interval_s: float = MIN_INTERVAL_S) -> None:
        if not valid_key(key):
            raise MassiveError('set [massive] key = "…" in settings.toml (a free key from '
                               "massive.com), then restart")
        self._key, self._opener, self._sleep, self._clock = key, opener, sleep, clock
        self._interval = min_interval_s
        self._last: float | None = None

    def bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        """Split-adjusted daily bars from ``start`` to ``end``, inclusive."""
        path = (f"/v2/aggs/ticker/{urllib.parse.quote(ticker)}/range/1/day/"
                f"{start.isoformat()}/{end.isoformat()}")
        query = {"adjusted": "true", "sort": "asc", "limit": "50000"}
        return [_bar(r, ticker) for r in self._pages(path, query, ticker)]

    def dividends(self, ticker: str, since: date, until: date) -> list[Dividend]:
        """Dividends gone ex from ``since`` to ``until``, inclusive. A declared
        dividend not yet ex has no split-adjusted amount, and adjusts nothing."""
        query = {"ticker": ticker, "ex_dividend_date.gte": since.isoformat(),
                 "ex_dividend_date.lte": until.isoformat(),
                 "sort": "ex_dividend_date.asc", "limit": "5000"}
        return [_dividend(r, ticker) for r in self._pages("/stocks/v1/dividends", query, ticker)]

    def _pages(self, path: str, query: dict[str, str], ticker: str) -> list[dict]:
        url = f"{HOST}{path}?{urllib.parse.urlencode(query)}"
        rows: list[dict] = []
        for _ in range(MAX_PAGES):
            body = self._get(url, ticker)
            rows.extend(body.get("results") or [])
            url = body.get("next_url") or ""
            if not url:
                return rows
            if not url.startswith(HOST):
                raise MassiveError(f"{ticker}: a next page outside {HOST}")
        raise MassiveError(f"{ticker}: more than {MAX_PAGES} pages")

    def _get(self, url: str, ticker: str) -> dict:
        if self._last is not None:
            wait = self._interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        request = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._key}", "User-Agent": USER_AGENT})
        try:
            with self._opener(request, timeout=TIMEOUT_S) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            hint = {401: "check [massive] key", 403: "not on your plan",
                    429: "over the rate limit; try again in a minute"}.get(exc.code, "")
            raise MassiveError(f"Massive returned HTTP {exc.code} for {ticker}"
                               + (f": {hint}" if hint else ""), exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MassiveUnreachable(f"couldn't reach Massive: "
                                     f"{getattr(exc, 'reason', exc)}") from None
        try:
            body = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise MassiveError(f"{ticker}: Massive's reply isn't JSON") from None
        if not isinstance(body, dict) or body.get("status") not in ("OK", "DELAYED"):
            status = body.get("status") if isinstance(body, dict) else None
            raise MassiveError(f"{ticker}: Massive's reply has status {status!r}")
        return body


def _bar(row: dict, ticker: str) -> Bar:
    try:
        day = datetime.fromtimestamp(int(row["t"]) / 1000, tz=UTC).date()
        return Bar(day, float(row["o"]), float(row["h"]), float(row["l"]), float(row["c"]),
                   float(row.get("v", 0.0)))
    except (KeyError, TypeError, ValueError):
        raise MassiveError(f"{ticker}: a bar without t, o, h, l and c") from None


def _dividend(row: dict, ticker: str) -> Dividend:
    try:
        return Dividend(date.fromisoformat(str(row["ex_dividend_date"])),
                        float(row["split_adjusted_cash_amount"]))
    except (KeyError, TypeError, ValueError):
        raise MassiveError(f"{ticker}: a dividend without ex_dividend_date and "
                           "split_adjusted_cash_amount") from None
