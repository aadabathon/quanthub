r"""Funds' holdings from SEC Form N-PORT, stdlib only (D62).

Every US registered fund, ETFs organized as unit trusts included, reports
its full portfolio on Form N-PORT. The public filings (``NPORT-P``) arrive
quarterly, about 60 days after the report date. They're XML, and they're
public under the SEC's fair-access policy, which ``SecClient`` already
follows (D47: a declared identity, at most ten requests a second). Every
request here goes through it.

**Finding a fund's latest report takes three steps:**

1. **The ticker.** SEC's fund ticker map (``company_tickers_mf.json``,
   ``{"fields": [cik, seriesId, classId, symbol], "data": [...]}``) gives a
   series trust's CIK and the fund's series id. A unit trust (SPY) has no
   series and isn't in it; its CIK comes from the company ticker map
   instead (``SecClient.tickers``).
2. **Its filings.** EDGAR's company browse, as an Atom feed, lists the
   series' (or CIK's) ``NPORT-P`` filings newest first. Each ``entry``'s
   ``content`` has ``accession-number``, ``filing-date`` and
   ``filing-type``.
3. **The report.** ``primary_doc.xml`` in the filing's folder,
   ``Archives/edgar/data/<cik>/<accession without dashes>/``.

**The report's layout** (namespace ``http://www.sec.gov/edgar/nport``; the
parser matches local names, so a namespace change doesn't break it):
* ``formData/genInfo``: ``regName``, ``regCik``, ``seriesName``,
  ``seriesId`` (absent for a unit trust), and ``repPdDate``, the
  portfolio's as-of date. ``repPdEnd`` is the fiscal year end and isn't
  used.
* ``formData/fundInfo/netAssets``.
* ``formData/invstOrSecs/invstOrSec``, one per holding: ``name``,
  ``lei``, ``cusip``, ``identifiers`` (``isin``, ``ticker`` or ``other``,
  each with a ``value`` attribute), ``balance``, ``units``, ``valUSD``,
  ``pctVal`` (percent of net assets, stored as a fraction), ``payoffProfile``,
  ``assetCat``, ``issuerCat``, ``invCountry``.
* ``N/A`` and blank fields are blank or None, never guessed.

**XML safety.** The documents come from sec.gov, and the stdlib parser
runs with Python 3.12's bundled expat, which limits entity expansion.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from quant_hub.data.holdings import FundHoldings, Holding

FUND_TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"
FEED_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={id}"
            "&type=NPORT-P&dateb=&owner=include&count={count}&output=atom")
DOCUMENT_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/primary_doc.xml"


class NportError(ValueError):
    """An N-PORT document or feed can't be read; the message says what."""


@dataclass(frozen=True)
class FilingRef:
    accession: str       # "0001410368-26-089410"
    filed: str           # ISO
    form: str            # "NPORT-P" or "NPORT-P/A"


def feed_url(identifier: str | int, count: int = 10) -> str:
    """The NPORT-P feed for a series id ("S000004310") or a CIK."""
    ident = identifier if isinstance(identifier, str) else f"{int(identifier):010d}"
    return FEED_URL.format(id=urllib.parse.quote(ident), count=count)


def document_url(cik: int, accession: str) -> str:
    return DOCUMENT_URL.format(cik=int(cik), folder=accession.replace("-", ""))


def parse_fund_tickers(payload: bytes) -> dict[str, tuple[int, str, str]]:
    """``company_tickers_mf.json`` as {ticker: (cik, series id, class id)}."""
    try:
        doc = json.loads(payload)
        fields = [str(f) for f in doc["fields"]]
        at = {name: fields.index(name) for name in ("cik", "seriesId", "classId", "symbol")}
        return {str(row[at["symbol"]]).upper(): (int(row[at["cik"]]), str(row[at["seriesId"]]),
                                                 str(row[at["classId"]]))
                for row in doc["data"] if row[at["symbol"]]}
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise NportError(f"the SEC's fund ticker map has an unexpected shape: {exc!r}") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element | None, name: str) -> ET.Element | None:
    if element is None:
        return None
    return next((c for c in element if _local(c.tag) == name), None)


def _text(element: ET.Element | None, name: str) -> str:
    found = _child(element, name)
    text = (found.text or "").strip() if found is not None else ""
    return "" if text.upper() == "N/A" else text


def _number(element: ET.Element | None, name: str) -> float | None:
    text = _text(element, name)
    try:
        value = float(text) if text else None
    except ValueError:
        return None
    return value if value is not None and math.isfinite(value) else None


def _parse(payload: bytes, what: str) -> ET.Element:
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise NportError(f"{what} isn't well-formed XML: {exc}") from exc


def parse_filing_feed(payload: bytes) -> list[FilingRef]:
    """The Atom feed's NPORT-P filings, newest first as EDGAR lists them."""
    root = _parse(payload, "the EDGAR filing feed")
    refs = []
    for entry in (e for e in root.iter() if _local(e.tag) == "entry"):
        content = _child(entry, "content")
        accession, filed, form = (_text(content, n) for n in
                                  ("accession-number", "filing-date", "filing-type"))
        if accession and filed and form.upper().startswith("NPORT-P"):
            refs.append(FilingRef(accession, filed, form))
    return refs


def parse_nport(payload: bytes, fund: str, accession: str, filed: str,
                series_id: str = "") -> FundHoldings:
    """One NPORT-P document as ``FundHoldings`` (module docstring). With
    ``series_id``, a document for another series is refused."""
    root = _parse(payload, f"{fund}'s N-PORT document")
    form = _child(root, "formData")
    gen, info = _child(form, "genInfo"), _child(form, "fundInfo")
    if gen is None:
        raise NportError(f"{fund}'s document has no formData/genInfo: is it an N-PORT?")
    found_series = _text(gen, "seriesId")
    if series_id and found_series and found_series != series_id:
        raise NportError(f"{fund}'s filing {accession} reports series {found_series}, "
                         f"not {series_id}")
    holdings = []
    secs = _child(form, "invstOrSecs")
    for sec in (s for s in (secs if secs is not None else ()) if _local(s.tag) == "invstOrSec"):
        ids = _child(sec, "identifiers")
        attr = {name: (_child(ids, name).get("value", "").strip() if _child(ids, name) is not None
                       else "") for name in ("isin", "ticker")}
        pct = _number(sec, "pctVal")
        holdings.append(Holding(
            name=_text(sec, "name") or _text(sec, "title") or "(unnamed)",
            lei=_text(sec, "lei"), cusip=_text(sec, "cusip"), isin=attr["isin"],
            ticker=attr["ticker"].upper(), balance=_number(sec, "balance"),
            units=_text(sec, "units"), value_usd=_number(sec, "valUSD"),
            pct=pct / 100.0 if pct is not None else None, asset_cat=_text(sec, "assetCat"),
            issuer_cat=_text(sec, "issuerCat"), country=_text(sec, "invCountry"),
            payoff=_text(sec, "payoffProfile")))
    if not holdings:
        raise NportError(f"{fund}'s filing {accession} lists no holdings")
    cik_text = _text(gen, "regCik")
    try:
        return FundHoldings(
            fund=fund.upper(), cik=int(cik_text) if cik_text else 0,
            series_id=found_series, fund_name=_text(gen, "seriesName") or _text(gen, "regName"),
            report_date=_text(gen, "repPdDate"), accession=accession, filed=filed,
            net_assets=_number(info, "netAssets"), holdings=tuple(holdings))
    except ValueError as exc:
        raise NportError(f"{fund}'s filing {accession}: {exc}") from exc
