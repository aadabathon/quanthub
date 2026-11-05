"""FRED, the St. Louis Fed's economic data, through its API (HEDGE U2, D69).

Terms (checked 2026-11-03, D60):
- **A free API key**, from fredaccount.stlouisfed.org, kept in
  ``settings.toml`` as ``[fred] key`` like ``[sec] identity``. It's never
  committed, logged, or put in an error message.
- **The credit line** "This product uses the FRED® API but is not endorsed
  or certified by the Federal Reserve Bank of St. Louis." is shown where a
  FRED series is (it's every series' citation).
- **Third-party series** carry their owner's copyright in FRED's notes and
  are for personal use. VIXCLS is Cboe's. Cboe's own site refuses storing
  (D60); FRED's personal-use terms are how VIX history comes in.

The series (``CATALOG``):
- ``VIXCLS``: the Cboe VIX at the close, in vol points, from 1990. HEDGE's
  option programs mark their puts from it (U1 §3).
- ``DTB3``: the 3-month T-bill, secondary market, discount basis, percent,
  from 1954.
- ``DGS3MO``: the 3-month Treasury at constant maturity, percent, from
  1981. REGIME.md's cash rate names it.

Each is stored as FRED publishes it (unit ``percent``). The models convert.

The observations endpoint returns JSON. Each observation is a date and a
value as text, with "." where there's none (a holiday). Those are left out,
never filled. One request per series, up to FRED's 100,000 observations.
The client is stdlib only: a plain User-Agent, a timeout, and typed errors.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from quant_hub.data.series import FetchedSeries, SeriesMeta

OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
SOURCE = "fred"
CREDIT = ("This product uses the FRED® API but is not endorsed or certified by the "
          "Federal Reserve Bank of St. Louis.")
USER_AGENT = "QuantHub/1.0 (personal research)"
TIMEOUT_S = 60.0
_KEY = re.compile(r"[a-z0-9]{32}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class FredSeries:
    fred_id: str          # "VIXCLS"
    title: str
    unit: str
    frequency: str
    owner: str = ""       # a third party's copyright, from FRED's notes

    @property
    def series(self) -> str:
        """The store's id: FRED's, in lower case."""
        return self.fred_id.lower()

    @property
    def citation(self) -> str:
        return f"{CREDIT} {self.owner}".strip()


CATALOG = {
    s.fred_id: s for s in (
        FredSeries("VIXCLS", "CBOE Volatility Index (VIX), close", "percent", "daily",
                   "Source: Chicago Board Options Exchange, which holds its copyright; "
                   "personal use only."),
        FredSeries("DTB3", "3-month Treasury bill, secondary market rate, discount basis",
                   "percent", "daily"),
        FredSeries("DGS3MO", "3-month Treasury, constant maturity", "percent", "daily"),
    )
}


class FredError(Exception):
    """Fetching or reading a FRED series failed; the message says what to do
    and never carries the key."""


def valid_key(key: str) -> bool:
    """FRED's keys are 32 lower-case letters and digits."""
    return bool(_KEY.fullmatch(key))


def observations_url(fred_id: str, key: str) -> str:
    query = urllib.parse.urlencode({"series_id": fred_id, "api_key": key, "file_type": "json"})
    return f"{OBSERVATIONS_URL}?{query}"


class FredClient:
    """Fetches observations (module docstring). ``opener`` is injectable for
    tests."""

    def __init__(self, key: str, timeout_s: float = TIMEOUT_S,
                 opener: Callable = urllib.request.urlopen) -> None:
        if not valid_key(key):
            raise FredError('set [fred] key = "…" in settings.toml (a free key from '
                            "fredaccount.stlouisfed.org, 32 letters and digits), then restart")
        self._key = key
        self._timeout = timeout_s
        self._opener = opener

    def observations(self, series: FredSeries) -> bytes:
        request = urllib.request.Request(observations_url(series.fred_id, self._key),
                                         headers={"User-Agent": USER_AGENT})
        try:
            with self._opener(request, timeout=self._timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise FredError(f"FRED returned HTTP {exc.code} for {series.fred_id}: "
                            f"{_error_message(exc)}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise FredError(f"couldn't reach FRED: {reason}") from None


def _error_message(exc: urllib.error.HTTPError) -> str:
    """FRED's own words for a refused request (a bad key, an unknown
    series), never the request's URL, which holds the key."""
    try:
        body = json.loads(exc.read().decode("utf-8"))
        return str(body.get("error_message", "")).strip() or "no reason given"
    except (ValueError, OSError, AttributeError):
        return "no reason given"


def parse_observations(series: FredSeries, payload: bytes) -> FetchedSeries:
    """FRED's JSON as one ``FetchedSeries`` (module docstring). Raises
    ``FredError`` naming what's wrong with the payload."""
    try:
        body = json.loads(payload.decode("utf-8"))
        rows = body["observations"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise FredError(f"{series.fred_id}: FRED's reply isn't its observations JSON") from None
    dates: list[str] = []
    values: list[float] = []
    for row in rows:
        day, text = str(row.get("date", "")), str(row.get("value", "")).strip()
        if not _DATE.fullmatch(day):
            raise FredError(f"{series.fred_id}: {day!r} isn't a date")
        if text in {".", ""}:
            continue
        try:
            values.append(float(text))
        except ValueError:
            raise FredError(f"{series.fred_id}: {text!r} on {day} isn't a number") from None
        dates.append(day)
    if not dates:
        raise FredError(f"{series.fred_id}: FRED returned no values")
    meta = SeriesMeta(SOURCE, series.series, series.title, series.unit, series.frequency,
                      series.citation, f"FRED {series.fred_id}")
    return FetchedSeries(meta, tuple(dates), tuple(values))
