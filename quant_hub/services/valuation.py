"""The Valuation page's data: companies, prices, the screen, saved records (W6).

* ``company``: a ticker or CIK to a ``FinancialHistory``, through the SEC
  fundamentals service (W5, D47).
* ``price``: the named source's quote, else its last close, with which one
  it was (the Options Lab's rule, D23). A typed price is the page's own.
* ``screen``: the owner's batch screener. Each company is seeded from its
  own history (``models.dcf.assumptions_from_history``), valued and priced,
  then the rows are ranked cheapest first. A failure is a row with its
  reason; it never ends the run.
* ``save`` / ``saved`` / ``reopen``: immutable records (D48) with a
  fingerprint of the filing data, so a reopened valuation says when the
  filings changed since.
* ``write_csv``: the screen as CSV (D43: no xlsx).
* ``valuation_years``: the stub s for "value as of today" (``models.dcf``),
  or 0 with the reason when the latest filing is a year old or more.

Blocking; call from the TaskRunner worker, except the two pure helpers.
"""

from __future__ import annotations

import csv
import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from quant_hub.broker.types import BrokerError
from quant_hub.data.fundamentals import FinancialHistory
from quant_hub.data.types import PriceBasis
from quant_hub.models.dcf import (
    Assumptions,
    BatchConfig,
    ValuationError,
    ValuationResult,
    assumptions_from_history,
    implied_growth,
    implied_wacc,
    market_comparison,
    value,
    years_since,
)
from quant_hub.services.fundamentals import FundamentalsService
from quant_hub.services.market_service import MarketService
from quant_hub.storage.valuation_store import SavedValuation, ValuationStore

_logger = logging.getLogger(__name__)

PRICE_LOOKBACK = timedelta(days=14)     # enough sessions to find a last close
SCREEN_COLUMNS = ("ticker", "company", "price", "price_basis", "value_per_share",
                  "margin_of_safety", "upside", "implied_growth", "seed_growth",
                  "seed_ebit_margin", "terminal_share", "missing_fields", "error")


@dataclass(frozen=True)
class Price:
    value: float
    basis: str                  # "quote · delayed", "last close", "typed"
    source: str
    as_of: datetime


@dataclass(frozen=True)
class ScreenRow:
    """One company in the screen (module docstring); NaN where unknown."""

    identifier: str
    ticker: str = ""
    company: str = ""
    price: float = math.nan
    price_basis: str = ""
    value_per_share: float = math.nan
    margin_of_safety: float = math.nan
    upside: float = math.nan
    implied_growth: tuple[float, ...] = ()
    seed_growth: float = math.nan
    seed_ebit_margin: float = math.nan
    terminal_share: float = math.nan
    missing_fields: str = ""
    error: str = ""


def rank(rows: Sequence[ScreenRow]) -> list[ScreenRow]:
    """Cheapest first: highest margin of safety on top, unknown margins last
    (the original's ``rank_screen``)."""
    known = [r for r in rows if math.isfinite(r.margin_of_safety)]
    unknown = [r for r in rows if not math.isfinite(r.margin_of_safety)]
    return sorted(known, key=lambda r: -r.margin_of_safety) + unknown


def refusal(exc: ValuationError) -> str:
    """A ``ValuationError``'s missing inputs on one line, each with its reason."""
    lines = str(exc).splitlines()
    return "; ".join(line.strip(" -") for line in lines[1:-1]) or str(exc)


def valuation_years(hist: FinancialHistory, on: date) -> tuple[float, str]:
    """s for valuing as of ``on`` (``models.dcf``), with a note when it can't be."""
    latest = hist.latest()
    if latest is None:
        return 0.0, ""
    s = years_since(latest.period_end, on)
    if s >= 1:
        return 0.0, (f"the latest fiscal year ended {latest.period_end}, over a year before "
                     f"{on}: valued as of that date")
    return max(s, 0.0), ""


class ValuationService:
    def __init__(self, fundamentals: FundamentalsService, market: MarketService,
                 store: ValuationStore, now: Callable[[], datetime] = datetime.now) -> None:
        self._fundamentals = fundamentals
        self._market = market
        self._store = store
        self._now = now

    @property
    def has_identity(self) -> bool:
        return self._fundamentals.has_identity

    @property
    def price_sources(self) -> list[str]:
        return self._market.source_names

    def company(self, identifier: str, refresh: bool = False) -> FinancialHistory:
        return self._fundamentals.history(identifier, refresh=refresh)

    def price(self, symbol: str, source: str) -> Price:
        """The source's quote, else its last close (module docstring)."""
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("no ticker to price: load a company by ticker, or type a price")
        try:
            quote = self._market.get_quote(symbol, source=source)
            return Price(quote.price, f"quote · {quote.mode.value}", source, quote.timestamp)
        except BrokerError as exc:
            _logger.info("%s: no quote from %s (%s); using last close", symbol, source, exc)
        end = self._now()
        history = self._market.get_history(symbol, end - PRICE_LOOKBACK, end, source=source,
                                           basis=PriceBasis.TRADED)
        return Price(float(history.closes.iloc[-1]), "last close", source, history.end)

    def screen(self, identifiers: Sequence[str], source: str | None,
               cfg: BatchConfig = BatchConfig(), template: Assumptions = Assumptions(),
               as_of_today: bool = False) -> list[ScreenRow]:
        """Seed, value and price each company; failures are rows (module
        docstring). ``template`` carries the page's conventions (terminal
        flow, securities netting)."""
        rows = []
        for identifier in identifiers:
            try:
                hist = self.company(identifier)
                seeded = assumptions_from_history(hist, cfg)
                a = replace(seeded, terminal_method=template.terminal_method,
                            net_marketable_securities=template.net_marketable_securities)
                if as_of_today:
                    a = replace(a, valuation_years=valuation_years(hist, self._now().date())[0])
                ticker = hist.ticker or identifier.upper()
                base = dict(identifier=identifier, ticker=ticker, company=hist.company or "",
                            seed_growth=float(a.revenue_growth),
                            seed_ebit_margin=float(a.ebit_margin),
                            missing_fields="; ".join(sorted(hist.missing)))
                try:
                    result = value(hist, a)
                except ValuationError as exc:
                    rows.append(ScreenRow(**base, error=refusal(exc)))
                    continue
                row = ScreenRow(**base, value_per_share=result.value_per_share,
                                terminal_share=result.terminal_share)
                if source is not None:
                    try:
                        p = self.price(ticker, source)
                        mc = market_comparison(result, p.value)
                        row = replace(row, price=p.value, price_basis=p.basis,
                                      margin_of_safety=mc.margin_of_safety, upside=mc.upside,
                                      implied_growth=implied_growth(hist, a, p.value))
                    except Exception as exc:              # a price gap is one row's problem
                        row = replace(row, error=f"no price: {exc}")
                rows.append(row)
            except Exception as exc:                      # one bad company can't sink the run
                _logger.warning("screen: %s: %s", identifier, exc)
                rows.append(ScreenRow(identifier, ticker=identifier.upper(), error=str(exc)))
        return rank(rows)

    @staticmethod
    def write_csv(rows: Sequence[ScreenRow], path: str | Path) -> Path:
        path = Path(path)
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(SCREEN_COLUMNS)
            for r in rows:
                cells = asdict(r)
                cells["implied_growth"] = " / ".join(f"{g:.4f}" for g in r.implied_growth)
                writer.writerow(["" if isinstance(cells[c], float) and not math.isfinite(cells[c])
                                 else cells[c] for c in SCREEN_COLUMNS])
        return path

    # ----------------------------------------------------------- saved records
    def save(self, hist: FinancialHistory, assumptions: Assumptions, result: ValuationResult,
             price: Price | None, notes: str = "") -> int:
        latest = hist.latest()
        payload = {
            "cik": hist.cik, "currency": hist.currency, "fingerprint": hist.fingerprint(),
            "fiscal_year": latest.fiscal_year if latest else None,
            "period_end": latest.period_end if latest else None,
            "data": hist.sources.get("data", ""),
            "assumptions": asdict(assumptions),
            "result": {"value_per_share": result.value_per_share,
                       "enterprise_value": result.enterprise_value,
                       "equity_value": result.equity_value, "pv_terminal": result.pv_terminal,
                       "terminal_share": result.terminal_share, "net_debt": result.net_debt,
                       "shares": result.shares, "base_revenue": result.base_revenue},
            "price": None,
        }
        if price is not None:
            mc = market_comparison(result, price.value)
            payload["price"] = {"value": price.value, "basis": price.basis,
                                "source": price.source, "as_of": price.as_of.isoformat(),
                                "margin_of_safety": mc.margin_of_safety, "upside": mc.upside,
                                "implied_growth": list(implied_growth(hist, assumptions,
                                                                      price.value)),
                                "implied_wacc": list(implied_wacc(hist, assumptions,
                                                                  price.value))}
        return self._store.save(hist.ticker or hist.cik or "", hist.company or "", payload,
                                self._now(), notes)

    def saved(self) -> list[SavedValuation]:
        return self._store.saved()

    def reopen(self, valuation_id: int) -> tuple[SavedValuation, Assumptions,
                                                 FinancialHistory | None, str]:
        """The record, its assumptions, today's history for it (None if it
        can't be had), and a note: unchanged, changed since, or why unknown."""
        record = self._store.load(valuation_id)
        assumptions = Assumptions(**record.payload["assumptions"])
        identifier = record.ticker or record.payload.get("cik") or ""
        try:
            hist = self.company(identifier)
        except Exception as exc:
            return record, assumptions, None, f"today's filing data is unavailable: {exc}"
        if hist.fingerprint() == record.payload.get("fingerprint"):
            return record, assumptions, hist, "the filing data is unchanged since it was saved"
        return record, assumptions, hist, ("the filing data has changed since it was saved "
                                           "(a restatement or a new filing): today's value differs")

    def save_notes(self, valuation_id: int, notes: str) -> None:
        self._store.save_notes(valuation_id, notes)
