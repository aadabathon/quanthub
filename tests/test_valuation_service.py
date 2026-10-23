"""The Valuation page's service (W6): prices with provenance, the screen,
CSV, saved records against a restatement, the valuation date, and the
v4 -> v5 migration. The original's ``rank_screen`` test is ported here."""

import csv
import math
import sqlite3
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.sources import SyntheticSource
from quant_hub.models.dcf import Assumptions, BatchConfig, ValuationError, value
from quant_hub.providers.sec_edgar import facts_from_companyfacts
from quant_hub.services.file_import import FileSource
from quant_hub.services.fundamentals import FundamentalsService
from quant_hub.services.market_service import MarketService
from quant_hub.services.valuation import (
    ScreenRow,
    ValuationService,
    rank,
    refusal,
    valuation_years,
)
from quant_hub.storage.bar_store import BarStore, InstrumentKey
from quant_hub.storage.fundamentals_store import FundamentalsStore
from quant_hub.storage.schema import SCHEMA_VERSION, prepare_store
from quant_hub.storage.valuation_store import ValuationStore
from test_sec_edgar import FY, companyfacts, flow

NOW = datetime(2026, 3, 27, 10, 0, 0)


class FakeSec:
    """Two filers: DEMO, and NODEBT (the same filings without debt tags), with
    ten shares so a share is worth hundreds of dollars ($423.74 seeded)."""

    shares = {"WeightedAverageNumberOfDilutedSharesOutstanding": {"shares": [flow(2025, 10.0, "A25")]}}
    docs = {1234: companyfacts(**shares), 5678: companyfacts(
        CommercialPaper=None, LongTermDebtCurrent=None, LongTermDebtNoncurrent=None,
        LongTermDebt=None, **shares)}

    def tickers(self):
        return {"DEMO": (1234, "DEMO Corp"), "NODEBT": (5678, "No Debt Inc")}

    def companyfacts(self, cik):
        return self.docs[cik]


@pytest.fixture
def parts():
    facts = FundamentalsStore(":memory:")
    bars = BarStore(":memory:")
    dates = pd.bdate_range("2026-02-02", "2026-03-26")
    bars.insert_bars(InstrumentKey("DEMO"), "file", "1 day",
                     pd.DataFrame({"close": np.linspace(280.0, 300.0, len(dates))}, index=dates))
    market = MarketService(sources=[SyntheticSource(), FileSource(bars)])
    fundamentals = FundamentalsService(facts, "Test Person test@example.com",
                                       client_factory=lambda _i: FakeSec(), now=lambda: NOW)
    store = ValuationStore(":memory:")
    yield ValuationService(fundamentals, market, store, now=lambda: NOW), facts
    facts.close()
    bars.close()
    store.close()


class TestPrices:
    def test_a_quote_when_the_source_has_one(self, parts):
        service, _ = parts
        p = service.price("demo", "synthetic")
        assert p.value > 0 and p.basis.startswith("quote") and p.source == "synthetic"

    def test_else_the_last_close_said_so(self, parts):
        service, _ = parts
        p = service.price("DEMO", "file")                  # files have no quotes
        assert (p.value, p.basis) == (pytest.approx(300.0), "last close")

    def test_no_ticker_is_explained(self, parts):
        with pytest.raises(ValueError, match="type a price"):
            parts[0].price("  ", "synthetic")


class TestScreen:
    def test_ranked_cheapest_first_with_failures_as_rows(self, parts):
        service, _ = parts
        rows = service.screen(["DEMO", "NODEBT", "NOPE"], "file", BatchConfig())
        assert [r.ticker for r in rows][-2:] in (["NODEBT", "NOPE"], ["NOPE", "NODEBT"])
        demo = rows[0]
        assert demo.ticker == "DEMO" and demo.company == "DEMO Corp"
        assert demo.price == pytest.approx(300.0) and demo.price_basis == "last close"
        assert math.isfinite(demo.margin_of_safety) and len(demo.implied_growth) >= 1
        nodebt = next(r for r in rows if r.ticker == "NODEBT")
        assert "net debt by hand" in nodebt.error and math.isnan(nodebt.value_per_share)
        nope = next(r for r in rows if r.ticker == "NOPE")
        assert "try its CIK" in nope.error

    def test_without_a_source_rows_are_valued_but_unpriced(self, parts):
        rows = parts[0].screen(["DEMO"], None)
        assert math.isfinite(rows[0].value_per_share) and math.isnan(rows[0].price)

    def test_rank_cheapest_first_unknown_last(self):
        # The original's test_rank_cheapest_first_nan_last, ported.
        ranked = rank([ScreenRow("A", margin_of_safety=-0.5), ScreenRow("B", margin_of_safety=0.3),
                       ScreenRow("C")])
        assert [r.identifier for r in ranked] == ["B", "A", "C"]

    def test_csv_export(self, parts, tmp_path):
        service, _ = parts
        rows = service.screen(["DEMO", "NOPE"], "file")
        path = service.write_csv(rows, tmp_path / "screen.csv")
        with path.open(encoding="utf-8") as f:
            table = list(csv.DictReader(f))
        assert [r["ticker"] for r in table] == ["DEMO", "NOPE"]
        assert float(table[0]["price"]) == pytest.approx(300.0) and table[1]["price"] == ""
        assert table[0]["implied_growth"] and "try its CIK" in table[1]["error"]


class TestSaved:
    def test_save_then_reopen_and_see_a_restatement(self, parts):
        service, facts = parts
        hist = service.company("DEMO")
        a = Assumptions(revenue_growth=[0.08, 0.07, 0.06, 0.05, 0.04])
        result = value(hist, a)
        price = service.price("DEMO", "file")
        vid = service.save(hist, a, result, price)
        record, assumptions, today, note = service.reopen(vid)
        assert assumptions == a and "unchanged" in note
        assert record.payload["result"]["value_per_share"] == pytest.approx(result.value_per_share)
        assert record.payload["price"]["basis"] == "last close"
        assert record.payload["price"]["margin_of_safety"] == pytest.approx(
            1 - 300.0 / result.value_per_share)
        # A later 10-K restates FY2025 revenue: the record says the filings changed.
        doc = companyfacts()
        doc["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"][
            "USD"].append({**flow(2025, 1010.0, "A25"), "accn": "A26", "filed": "2026-03-01"})
        doc["facts"]["us-gaap"].update(
            {k: {"units": v} for k, v in FakeSec.shares.items()})
        facts.save_facts(1234, "DEMO Corp", facts_from_companyfacts(doc), NOW)
        _record, _a, today, note = service.reopen(vid)
        assert "changed" in note and today.latest().revenue == 1010.0

    def test_unpriced_valuations_save_too(self, parts):
        service, _ = parts
        hist = service.company("DEMO")
        vid = service.save(hist, Assumptions(), value(hist, Assumptions()), None)
        assert service.saved()[0].id == vid and service.saved()[0].payload["price"] is None


class TestHelpers:
    def test_the_valuation_date(self, parts):
        hist = parts[0].company("DEMO")
        s, note = valuation_years(hist, date(2026, 3, 27))
        assert s == pytest.approx((date(2026, 3, 27) - date(2025, 9, 27)).days / 365.25)
        assert note == ""
        s, note = valuation_years(hist, date(2026, 10, 21))
        assert s == 0.0 and "over a year" in note

    def test_a_refusal_lists_every_reason(self):
        from quant_hub.data.fundamentals import FinancialHistory
        h = FinancialHistory()
        h.note_missing("revenue", "no revenue tag")
        h.note_missing("total_debt", "no debt tags")
        with pytest.raises(ValuationError) as info:
            value(h, Assumptions())
        text = refusal(info.value)
        assert "no revenue tag" in text and "no debt tags" in text and "shares" in text

    def test_the_fingerprint_follows_the_numbers(self, parts):
        hist = parts[0].company("DEMO")
        before = hist.fingerprint()
        assert parts[0].company("DEMO").fingerprint() == before
        hist.latest().revenue += 1.0
        assert hist.fingerprint() != before


def test_a_v4_store_migrates_to_v5_with_a_backup(tmp_path):
    path = tmp_path / "market_data.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                     "applied_at TEXT NOT NULL, backup TEXT)")
        conn.execute("INSERT INTO schema_migrations VALUES (4, 'earlier', NULL)")
    result = prepare_store(path)
    assert (result.migrated_from, result.version) == (4, SCHEMA_VERSION) and result.backup
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='valuations'").fetchall()
    store = ValuationStore(path)
    vid = store.save("DEMO", "DEMO Corp", {"x": 1}, NOW, notes="first")
    store.save_notes(vid, "edited")
    assert store.load(vid).notes == "edited" and store.load(vid).payload == {"x": 1}
    store.close()
