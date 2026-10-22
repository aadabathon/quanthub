r"""SEC EDGAR fundamentals through the companyfacts API, stdlib only (W5).

The SEC publishes every XBRL fact a company has filed as one JSON document
per filer ("companyfacts"), public under its fair-access policy: requests
must declare who is asking (a name and an email in the User-Agent), and stay
at or under ten a second. This module is the only code that talks to it.

* ``SecClient`` fetches and does nothing else: the ticker map and a filer's
  companyfacts, with the identity header, gzip, a ten-a-second throttle,
  timeouts and typed errors.
* ``facts_from_companyfacts`` flattens the JSON into ``data.fundamentals.Fact``
  records as filed, keeping only the concepts below.
* ``history_from_facts`` maps facts to a ``FinancialHistory``: pure, with
  no network, by the rules below.

Mapping rules
-------------
* **Annual facts only:** forms 10-K, 20-F or 40-F (and their amendments).
  A flow (income statement, cash flow) must span a fiscal year, 350 to 380
  days (52- and 53-week years included); 10-Qs and a 10-K's three-month
  quarters drop out. A balance-sheet line is an instant at the year end.
* **Fiscal years** are the distinct annual period ends, labelled by the
  year they end in (the original's convention), latest ``years`` kept. A
  52/53-week year ending in the first week of January is labelled the year
  before, so a filer whose years end on Jan 1 and then Dec 31 doesn't get
  two of one label; any other collision falls back to consecutive labels
  counted back from the latest.
* **Restatements:** each concept has one fact per period per filing. The
  latest-filed fact wins, so a restated year shows its restated value, and
  its accession is recorded.
* **Concepts:** each line has a priority list (below); the first concept
  with a fact for that year wins, so a filer's tag change between years (an
  older ``SalesRevenueNet`` before ``RevenueFromContract…``) is followed.
  Capex and D&A are stored positive.
* **Debt** sums non-overlapping parts, so no layer counts twice. The current
  part is ``DebtCurrent`` when filed, else commercial paper + short-term
  borrowings + current long-term debt. The noncurrent part is
  ``LongTermDebtNoncurrent`` when filed, else ``LongTermDebt`` less its
  current portion. (The original summed every tag present, which counts
  current maturities twice for a filer that tags ``LongTermDebt`` beside
  its parts.) One layout can still overlap: ``DebtCurrent`` with a
  ``LongTermDebt`` total and no current portion tagged. The source line
  names the parts, for the check against the 10-K.
* **Cash** is the face line, strict cash and equivalents. **Marketable
  securities** are the current plus the noncurrent line, for the DCF's
  optional netting.
* **Diluted shares:** the latest year's weighted-average diluted count;
  failing that, the cover page's shares outstanding (period-end, basic),
  and the source says so.
* **Currency:** the revenue facts' unit, USD preferred.
* **Gaps:** a line the latest year lacks gets its reason in ``missing``, and
  so does a filer with no annual facts at all. Nothing is guessed (the
  loud-failure contract, ``data/fundamentals.py``).
* **Not mapped yet:** IFRS filers (the ``ifrs-full`` taxonomy, many 20-Fs)
  come back with no annual facts, named as such; and an unlevered DCF is
  the wrong model for banks, insurers and REITs whatever the mapping (the
  original README's warning).

The fair-access policy and the throttle: ``MIN_INTERVAL_S`` spaces requests
at least 0.1 s apart, the SEC's ten a second. A Valuation-page lookup makes
one or two.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import date

from quant_hub.data.fundamentals import Fact, FinancialHistory, YearData

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
MIN_INTERVAL_S = 0.1                     # the SEC's fair-access limit: ten requests a second
TIMEOUT_S = 30.0
ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
ANNUAL_DAYS = (350, 380)                 # a fiscal year's span, 52- and 53-week years included

# Duration (flow) lines: the first concept with a fact for a year wins.
DURATION = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueGoodsNet"),
    "operating_income": ("OperatingIncomeLoss",),
    "income_before_tax": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"),
    "income_tax_expense": ("IncomeTaxExpenseBenefit",),
    "depreciation_amortization": ("DepreciationDepletionAndAmortization",
                                  "DepreciationAmortizationAndAccretionNet",
                                  "DepreciationAndAmortization", "Depreciation"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
}
POSITIVE = ("depreciation_amortization", "capex")
# Instant (balance-sheet) lines.
INSTANT = {
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue", "Cash"),
}
DEBT_CURRENT_TOTAL = "DebtCurrent"
DEBT_CURRENT_PARTS = ("CommercialPaper", "ShortTermBorrowings", "LongTermDebtCurrent")
DEBT_NONCURRENT = "LongTermDebtNoncurrent"
DEBT_TOTAL_LONG = "LongTermDebt"
SECURITIES_CURRENT = ("MarketableSecuritiesCurrent", "ShortTermInvestments",
                      "AvailableForSaleSecuritiesDebtSecuritiesCurrent")
SECURITIES_NONCURRENT = ("MarketableSecuritiesNoncurrent",
                         "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent")
SHARES_DILUTED = "WeightedAverageNumberOfDilutedSharesOutstanding"
COVER_SHARES = ("dei", "EntityCommonStockSharesOutstanding")

WANTED = frozenset(
    {("us-gaap", c) for tags in (*DURATION.values(), *INSTANT.values()) for c in tags}
    | {("us-gaap", c) for c in (DEBT_CURRENT_TOTAL, *DEBT_CURRENT_PARTS, DEBT_NONCURRENT,
                               DEBT_TOTAL_LONG, *SECURITIES_CURRENT, *SECURITIES_NONCURRENT,
                               SHARES_DILUTED)}
    | {COVER_SHARES})

_EDGAR_CIK = re.compile(r"/data/(\d+)[/-]", re.IGNORECASE)
_CIK_PREFIX = re.compile(r"^cik[-_]?(\d+)$", re.IGNORECASE)
_IDENTITY = re.compile(r"\S+\s+.*\S+@\S+\.\S+")


class SecError(Exception):
    """A request to the SEC failed; the message says what to do."""


def parse_identifier(text: str) -> int | str:
    """A CIK (int) from an EDGAR URL, ``CIK0000320193`` or bare digits, else
    the ticker, uppercased (the owner's ``resolve.py`` rules)."""
    s = text.strip()
    if not s:
        raise ValueError("enter a ticker or CIK")
    if s.lower().startswith(("http://", "https://")):
        m = _EDGAR_CIK.search(s)
        if not m:
            raise ValueError(f"could not find a CIK in {s!r}: use an EDGAR /data/<CIK>/ URL, "
                             "a ticker or a CIK")
        return int(m.group(1))
    m = _CIK_PREFIX.match(s)
    if m:
        return int(m.group(1))
    if s.isdigit():
        return int(s)
    return s.upper()


class SecClient:
    """Fetches from the SEC under its fair-access policy (module docstring).

    ``identity`` is the declared User-Agent, "Name email@example.com".
    ``opener``, ``clock`` and ``sleep`` are injectable for tests.
    """

    def __init__(self, identity: str, timeout_s: float = TIMEOUT_S,
                 opener: Callable = urllib.request.urlopen,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not _IDENTITY.fullmatch(identity.strip()):
            raise ValueError('the SEC needs an identity of the form "Your Name you@example.com" '
                             "([sec] identity in settings.toml)")
        self._identity = identity.strip()
        self._timeout = timeout_s
        self._opener, self._clock, self._sleep = opener, clock, sleep
        self._lock = threading.Lock()
        self._last = -math.inf

    def tickers(self) -> dict[str, tuple[int, str]]:
        """Every listed ticker: ``{"AAPL": (320193, "Apple Inc."), ...}``."""
        doc = json.loads(self._get(TICKERS_URL))
        return {str(row["ticker"]).upper(): (int(row["cik_str"]), str(row["title"]))
                for row in doc.values()}

    def companyfacts(self, cik: int) -> dict:
        return json.loads(self._get(FACTS_URL.format(cik=int(cik))))

    def _get(self, url: str) -> bytes:
        with self._lock:                                 # one request at a time, spaced
            wait = self._last + MIN_INTERVAL_S - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
        request = urllib.request.Request(url, headers={"User-Agent": self._identity,
                                                       "Accept-Encoding": "gzip"})
        try:
            with self._opener(request, timeout=self._timeout) as response:
                data = response.read()
                encoding = response.headers.get("Content-Encoding", "")
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise SecError("the SEC refused the request (HTTP 403). Its fair-access policy "
                               'needs [sec] identity = "Your Name you@example.com" in '
                               "settings.toml") from exc
            if exc.code == 404:
                raise SecError("the SEC has no XBRL facts for that company (HTTP 404): it may "
                               "not file 10-Ks, or the CIK is wrong") from exc
            raise SecError(f"the SEC returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise SecError(f"couldn't reach the SEC: {reason}") from exc
        return gzip.decompress(data) if encoding == "gzip" else data


def facts_from_companyfacts(doc: dict, wanted: frozenset = WANTED) -> list[Fact]:
    """The companyfacts JSON as ``Fact`` records, as filed, keeping only
    ``wanted`` (taxonomy, concept) pairs. Malformed entries are skipped."""
    facts: list[Fact] = []
    for taxonomy, concepts in (doc.get("facts") or {}).items():
        for concept, body in concepts.items():
            if (taxonomy, concept) not in wanted:
                continue
            for unit, entries in (body.get("units") or {}).items():
                for e in entries:
                    try:
                        facts.append(Fact(
                            taxonomy, concept, unit, e.get("start"), e["end"], float(e["val"]),
                            int(e["fy"]) if e.get("fy") is not None else None, e.get("fp"),
                            e["form"], e["filed"], e["accn"]))
                    except (KeyError, TypeError, ValueError):
                        continue
    return facts


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _annual_flow(f: Fact) -> bool:
    return (f.form in ANNUAL_FORMS and f.start is not None
            and ANNUAL_DAYS[0] <= _days(f.start, f.end) <= ANNUAL_DAYS[1])


def _annual_instant(f: Fact) -> bool:
    return f.form in ANNUAL_FORMS and f.start is None


class _Latest:
    """The latest-filed fact per (concept, period end), among those a
    predicate admits, in one unit: restatements win."""

    def __init__(self, facts: Iterable[Fact], admit: Callable[[Fact], bool], unit: str):
        self._best: dict[tuple[str, str], Fact] = {}
        for f in facts:
            if f.unit != unit or not admit(f):
                continue
            key = (f.concept, f.end)
            held = self._best.get(key)
            if held is None or (f.filed, f.accession) > (held.filed, held.accession):
                self._best[key] = f

    def get(self, concept: str, end: str) -> Fact | None:
        return self._best.get((concept, end))

    def first(self, concepts: Iterable[str], end: str) -> Fact | None:
        for concept in concepts:
            fact = self.get(concept, end)
            if fact is not None:
                return fact
        return None

    def ends(self, concepts: Iterable[str]) -> set[str]:
        wanted = set(concepts)
        return {end for (concept, end) in self._best if concept in wanted}


def _labels(ends: list[str]) -> list[int]:
    """Fiscal-year labels for sorted period ends (module docstring)."""
    labels = []
    for end in ends:
        d = date.fromisoformat(end)
        labels.append(d.year - 1 if d.month == 1 and d.day <= 7 else d.year)
    if len(set(labels)) < len(labels):
        labels = [labels[-1] - (len(labels) - 1 - i) for i in range(len(labels))]
    return labels


def _tag(f: Fact) -> str:
    return f"{f.taxonomy}:{f.concept} ({f.form} filed {f.filed}, {f.accession})"


def _currency(facts: list[Fact]) -> str:
    revenue = set(DURATION["revenue"])
    units = {f.unit for f in facts if f.concept in revenue}
    if "USD" in units or not units:
        return "USD"
    return sorted(units)[0]


def _debt(latest: _Latest, end: str) -> tuple[float | None, list[Fact]]:
    """Non-overlapping debt parts at ``end`` (module docstring)."""
    used: list[Fact] = []
    current = latest.get(DEBT_CURRENT_TOTAL, end)
    if current is not None:
        used.append(current)
        current_value = current.value
    else:
        parts = [f for f in (latest.get(c, end) for c in DEBT_CURRENT_PARTS) if f is not None]
        used.extend(parts)
        current_value = sum(f.value for f in parts) if parts else None
    noncurrent = latest.get(DEBT_NONCURRENT, end)
    total_long = latest.get(DEBT_TOTAL_LONG, end)
    if noncurrent is not None:
        used.append(noncurrent)
        noncurrent_value = noncurrent.value
    elif total_long is not None:
        used.append(total_long)
        current_part = latest.get("LongTermDebtCurrent", end)
        noncurrent_value = total_long.value - (current_part.value if current_part else 0.0)
    else:
        noncurrent_value = None
    if current_value is None and noncurrent_value is None:
        return None, []
    return (current_value or 0.0) + (noncurrent_value or 0.0), used


def history_from_facts(facts: list[Fact], years: int = 5, *, cik: int | None = None,
                       company: str | None = None,
                       ticker: str | None = None) -> FinancialHistory:
    """The module docstring's mapping rules: facts to fiscal years, loudly."""
    if years < 1:
        raise ValueError("years must be at least 1")
    currency = _currency(facts)
    flows = _Latest(facts, _annual_flow, currency)
    instants = _Latest(facts, _annual_instant, currency)
    hist = FinancialHistory(cik=str(cik) if cik is not None else None, company=company,
                            ticker=ticker, currency=currency)
    ends = sorted(flows.ends(c for tags in DURATION.values() for c in tags))[-years:]
    if not ends:
        hist.note_missing("financials", "no annual (10-K, 20-F or 40-F) facts in the SEC data: "
                          "the company may not file them, or files them without XBRL")
        hist.note_missing("revenue", "no annual revenue facts")
        hist.note_missing("total_debt", "no annual balance sheet")
        hist.note_missing("shares_diluted", "no annual share counts")
        return hist

    for end, label in zip(ends, _labels(ends)):
        line: dict[str, float | None] = {}
        for name, tags in DURATION.items():
            f = flows.first(tags, end)
            line[name] = None if f is None else (abs(f.value) if name in POSITIVE else f.value)
        for name, tags in INSTANT.items():
            f = instants.first(tags, end)
            line[name] = None if f is None else f.value
        line["total_debt"], _ = _debt(instants, end)
        cur = instants.first(SECURITIES_CURRENT, end)
        non = instants.first(SECURITIES_NONCURRENT, end)
        line["marketable_securities"] = (None if cur is None and non is None
                                         else (cur.value if cur else 0.0)
                                         + (non.value if non else 0.0))
        hist.years.append(YearData(label, end, **line))

    latest_end = ends[-1]
    for name, tags in DURATION.items():
        f = flows.first(tags, latest_end)
        if f is None:
            hist.note_missing(name, f"no {' / '.join(tags)} fact for the year ending {latest_end}")
        else:
            hist.note_source(name, _tag(f))
    for name, tags in INSTANT.items():
        f = instants.first(tags, latest_end)
        if f is None:
            hist.note_missing(name, f"no {' / '.join(tags)} fact at {latest_end}")
        else:
            hist.note_source(name, _tag(f))
    debt, parts = _debt(instants, latest_end)
    if debt is None:
        hist.note_missing("total_debt", f"no debt facts at {latest_end} (a debt-free filer tags "
                          "none): set net debt by hand")
    else:
        hist.note_source("total_debt", "sum(" + ", ".join(p.concept for p in parts) + f") at "
                         f"{latest_end}, latest filing {max(p.filed for p in parts)}")
    cur = instants.first(SECURITIES_CURRENT, latest_end)
    non = instants.first(SECURITIES_NONCURRENT, latest_end)
    if cur is None and non is None:
        hist.note_missing("marketable_securities", f"no marketable securities facts at {latest_end}")
    else:
        hist.note_source("marketable_securities",
                         "sum(" + ", ".join(f.concept for f in (cur, non) if f) + ")")

    shares = _Latest(facts, _annual_flow, "shares").get(SHARES_DILUTED, latest_end)
    if shares is not None:
        hist.shares_diluted = shares.value
        hist.note_source("shares_diluted", _tag(shares) + ", weighted-average diluted")
    else:
        cover = [f for f in facts if (f.taxonomy, f.concept) == COVER_SHARES
                 and f.unit == "shares" and f.start is None]
        if cover:
            best = max(cover, key=lambda f: (f.end, f.filed, f.accession))
            hist.shares_diluted = best.value
            hist.note_source("shares_diluted", _tag(best) + ": the cover page's shares outstanding "
                             f"at {best.end}, basic and period-end, not diluted")
        else:
            hist.note_missing("shares_diluted", f"no {SHARES_DILUTED} for the year ending "
                              f"{latest_end}, and no cover-page share count")
    return hist
