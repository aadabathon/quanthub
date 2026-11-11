"""SEC companyfacts (W5), offline: flattening, the mapping rules, identifiers
and the client, against a made-up filer in the API's own JSON shape.

The original adapter's tests carry over in intent: debt sums its parts
(commercial paper included) without double counting, prior periods resolve,
cash is the face line (companyfacts holds no dimensional breakdown rows, so
that one comes free), and a gap is a reason, never a number.
"""

import gzip
import io
import json
import math
import urllib.error

import pytest

from quant_hub.data.fundamentals import Fact
from quant_hub.models.dcf import Assumptions, ValuationError, value
from quant_hub.providers.sec_edgar import (
    MIN_INTERVAL_S,
    SecClient,
    SecError,
    facts_from_companyfacts,
    history_from_facts,
    parse_identifier,
)

IDENTITY = "Test Person test@example.com"
FY = {2021: ("2020-09-27", "2021-09-25"), 2022: ("2021-09-26", "2022-09-24"),
      2023: ("2022-09-25", "2023-09-30"), 2024: ("2023-10-01", "2024-09-28"),
      2025: ("2024-09-29", "2025-09-27")}
FILINGS = {"A22": ("10-K", "2022-10-28", 2022), "A23": ("10-K", "2023-11-03", 2023),
           "A24": ("10-K", "2024-11-01", 2024), "A25": ("10-K", "2025-10-31", 2025),
           "Q25": ("10-Q", "2025-08-01", 2025)}


def flow(year, val, accn):
    start, end = FY[year]
    form, filed, fy = FILINGS[accn]
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": fy, "fp": "FY",
            "form": form, "filed": filed}


def instant(end, val, accn):
    form, filed, fy = FILINGS[accn]
    return {"end": end, "val": val, "accn": accn, "fy": fy, "fp": "FY", "form": form,
            "filed": filed}


def companyfacts(**overrides):
    """DEMO: a September filer, five fiscal years, with a restatement, a tag
    switch, quarterly noise and both ways of tagging debt."""
    end25, end24 = FY[2025][1], FY[2024][1]
    us = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"USD": [
            flow(2025, 1000.0, "A25"), flow(2024, 950.0, "A24"), flow(2024, 950.0, "A25"),
            flow(2023, 900.0, "A23"), flow(2023, 905.0, "A24"), flow(2023, 905.0, "A25"),
            {"start": "2025-03-30", "end": "2025-06-28", "val": 240.0, "accn": "Q25", "fy": 2025,
             "fp": "Q3", "form": "10-Q", "filed": "2025-08-01"},
            {"start": "2025-06-29", "end": end25, "val": 260.0, "accn": "A25", "fy": 2025,
             "fp": "FY", "form": "10-K", "filed": "2025-10-31"}]},
        "SalesRevenueNet": {"USD": [flow(2022, 850.0, "A22"), flow(2021, 800.0, "A22")]},
        "OperatingIncomeLoss": {"USD": [flow(2025, 300.0, "A25"), flow(2024, 280.0, "A25")]},
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest":
            {"USD": [flow(2025, 290.0, "A25")]},
        "IncomeTaxExpenseBenefit": {"USD": [flow(2025, 50.0, "A25")]},
        "DepreciationDepletionAndAmortization": {"USD": [flow(2025, 40.0, "A25")]},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"USD": [flow(2025, 45.0, "A25")]},
        "AssetsCurrent": {"USD": [instant(end25, 500.0, "A25")]},
        "LiabilitiesCurrent": {"USD": [instant(end25, 400.0, "A25")]},
        "CashAndCashEquivalentsAtCarryingValue": {"USD": [instant(end25, 120.0, "A25")]},
        "MarketableSecuritiesCurrent": {"USD": [instant(end25, 60.0, "A25")]},
        "MarketableSecuritiesNoncurrent": {"USD": [instant(end25, 140.0, "A25")]},
        "CommercialPaper": {"USD": [instant(end25, 20.0, "A25"), instant(end24, 25.0, "A24")]},
        "LongTermDebtCurrent": {"USD": [instant(end25, 30.0, "A25"), instant(end24, 28.0, "A24")]},
        "LongTermDebtNoncurrent": {"USD": [instant(end25, 200.0, "A25")]},
        "LongTermDebt": {"USD": [instant(end24, 248.0, "A24")]},          # total, incl. current
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"shares": [flow(2025, 1.0e6, "A25")]},
        "NetIncomeLoss": {"USD": [flow(2025, 240.0, "A25")]},              # not read: dropped
    }
    dei = {"EntityCommonStockSharesOutstanding": {"shares": [instant("2025-10-17", 9.9e5, "A25")]}}
    us.update(overrides)
    us = {k: v for k, v in us.items() if v is not None}
    return {"cik": 1234, "entityName": "DEMO Corp", "facts": {"us-gaap": {
        k: {"label": k, "units": v} for k, v in us.items()},
        "dei": {k: {"label": k, "units": v} for k, v in dei.items()}}}


def demo_history(doc=None, years=5):
    return history_from_facts(facts_from_companyfacts(doc or companyfacts()), years, cik=1234,
                              company="DEMO Corp", ticker="DEMO")


# ------------------------------------------------------------------ flattening
def test_flattening_keeps_the_mapped_concepts_as_filed():
    doc = companyfacts()
    doc["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"].append({"val": 1.0})  # malformed
    facts = facts_from_companyfacts(doc)
    concepts = {f.concept for f in facts}
    assert "NetIncomeLoss" not in concepts and "EntityCommonStockSharesOutstanding" in concepts
    cash = [f for f in facts if f.concept == "CashAndCashEquivalentsAtCarryingValue"]
    assert cash == [Fact("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD", None,
                         FY[2025][1], 120.0, 2025, "FY", "10-K", "2025-10-31", "A25")]
    assert len([f for f in facts if f.concept == "OperatingIncomeLoss"]) == 2


# ------------------------------------------------------------- mapping rules
class TestMapping:
    def test_fiscal_years_restatements_and_tag_switches(self):
        hist = demo_history()
        assert [y.fiscal_year for y in hist.years] == [2021, 2022, 2023, 2024, 2025]
        revenue = {y.fiscal_year: y.revenue for y in hist.years}
        # FY2023 restated to 905 by the later 10-Ks; FY2021-22 only as SalesRevenueNet;
        # the 10-Q and the 10-K's three-month quarter never count.
        assert revenue == {2021: 800.0, 2022: 850.0, 2023: 905.0, 2024: 950.0, 2025: 1000.0}
        assert hist.latest().period_end == FY[2025][1]
        assert [y.fiscal_year for y in demo_history(years=2).years] == [2024, 2025]

    def test_sources_name_the_concept_and_the_filing(self):
        hist = demo_history()
        assert "RevenueFromContractWithCustomerExcludingAssessedTax" in hist.sources["revenue"]
        assert "10-K filed 2025-10-31, A25" in hist.sources["revenue"]
        assert hist.company == "DEMO Corp" and hist.ticker == "DEMO" and hist.cik == "1234"
        assert hist.currency == "USD"

    def test_debt_parts_never_overlap(self):
        years = {y.fiscal_year: y for y in demo_history().years}
        assert years[2025].total_debt == 20.0 + 30.0 + 200.0        # CP + current + noncurrent
        assert years[2024].total_debt == 25.0 + 28.0 + (248.0 - 28.0)   # total LTD less current
        source = demo_history().sources["total_debt"]
        assert "CommercialPaper" in source and "LongTermDebtNoncurrent" in source

    def test_a_current_debt_total_isnt_added_to_its_parts(self):
        end = FY[2025][1]
        hist = demo_history(companyfacts(
            DebtCurrent={"USD": [instant(end, 50.0, "A25")]},           # includes CP and current LTD
            LongTermDebtCurrent=None))
        assert hist.latest().total_debt == 50.0 + 200.0

    def test_cash_securities_and_working_capital(self):
        latest = demo_history().latest()
        assert latest.cash_and_equivalents == 120.0
        assert latest.marketable_securities == 60.0 + 140.0
        assert latest.net_working_capital() == 500.0 - 120.0 - 400.0

    def test_capex_and_da_are_stored_positive(self):
        hist = demo_history(companyfacts(
            PaymentsToAcquirePropertyPlantAndEquipment={"USD": [flow(2025, -45.0, "A25")]}))
        assert hist.latest().capex == 45.0 and hist.latest().depreciation_amortization == 40.0

    def test_shares_are_weighted_diluted_else_the_cover_page_said_so(self):
        hist = demo_history()
        assert hist.shares_diluted == 1.0e6
        assert "weighted-average diluted" in hist.sources["shares_diluted"]
        cover = demo_history(companyfacts(WeightedAverageNumberOfDilutedSharesOutstanding=None))
        assert cover.shares_diluted == 9.9e5
        assert "basic and period-end, not diluted" in cover.sources["shares_diluted"]

    def test_gaps_are_reasons_and_the_dcf_repeats_them(self):
        no_debt = demo_history(companyfacts(CommercialPaper=None, LongTermDebtCurrent=None,
                                            LongTermDebtNoncurrent=None, LongTermDebt=None))
        assert no_debt.latest().total_debt is None
        assert "set net debt by hand" in no_debt.missing["total_debt"]
        with pytest.raises(ValuationError, match="set net debt by hand"):
            value(no_debt, Assumptions())
        assert value(no_debt, Assumptions(net_debt_override=0.0)).value_per_share > 0

    def test_a_filer_without_annual_facts(self):
        quarterly = {"cik": 9, "entityName": "Q", "facts": {"us-gaap": {"Revenues": {"units": {
            "USD": [{"start": "2025-03-30", "end": "2025-06-28", "val": 1.0, "accn": "Q25",
                     "fy": 2025, "fp": "Q3", "form": "10-Q", "filed": "2025-08-01"}]}}}}}
        hist = history_from_facts(facts_from_companyfacts(quarterly))
        assert hist.years == [] and "no annual" in hist.missing["financials"]
        with pytest.raises(ValuationError):
            value(hist, Assumptions())

    def test_the_currency_follows_revenue(self):
        doc = {"cik": 7, "entityName": "EU", "facts": {"us-gaap": {"Revenues": {"units": {
            "EUR": [flow(2025, 700.0, "A25")]}}}}}
        hist = history_from_facts(facts_from_companyfacts(doc))
        assert hist.currency == "EUR" and hist.latest().revenue == 700.0

    def test_52_53_week_years_keep_unique_labels(self):
        doc = {"cik": 8, "entityName": "R", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"start": "2021-01-03", "end": "2022-01-01", "val": 1.0, "accn": "A", "fy": 2021,
             "fp": "FY", "form": "10-K", "filed": "2022-02-20"},
            {"start": "2022-01-02", "end": "2022-12-31", "val": 2.0, "accn": "B", "fy": 2022,
             "fp": "FY", "form": "10-K", "filed": "2023-02-20"}]}}}}}
        hist = history_from_facts(facts_from_companyfacts(doc))
        assert [y.fiscal_year for y in hist.years] == [2021, 2022]

    def test_the_history_values_a_company(self):
        r = value(demo_history(), Assumptions())
        assert r.base_revenue == 1000.0 and r.net_debt == 250.0 - 120.0 and r.shares == 1.0e6


# ----------------------------------------------------------------- identifiers
@pytest.mark.parametrize("text, expected", [
    ("aapl", "AAPL"), (" BRK-B ", "BRK-B"), ("320193", 320193), ("CIK0000320193", 320193),
    ("https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
     320193)])
def test_identifiers(text, expected):
    assert parse_identifier(text) == expected


@pytest.mark.parametrize("text", ["", "https://example.com/filing"])
def test_bad_identifiers(text):
    with pytest.raises(ValueError):
        parse_identifier(text)


# ---------------------------------------------------------------------- client
class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, gzipped: bool = True):
        super().__init__(gzip.compress(payload) if gzipped else payload)
        self.headers = {"Content-Encoding": "gzip"} if gzipped else {}


class FakeOpener:
    def __init__(self, payload=b"{}", error=None):
        self.payload, self.error, self.requests = payload, error, []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.payload)


class TestClient:
    def test_requests_declare_the_identity_and_are_decoded(self):
        opener = FakeOpener(json.dumps({"entityName": "X"}).encode())
        doc = SecClient(IDENTITY, opener=opener, sleep=lambda s: None).companyfacts(320193)
        request, timeout = opener.requests[0]
        assert doc == {"entityName": "X"}
        assert request.full_url.endswith("/CIK0000320193.json")
        assert request.get_header("User-agent") == IDENTITY and timeout > 0

    def test_requests_are_spaced_ten_a_second(self):
        slept = []
        client = SecClient(IDENTITY, opener=FakeOpener(), clock=lambda: 100.0,
                           sleep=slept.append)
        client.companyfacts(1)
        client.companyfacts(1)
        assert slept == [pytest.approx(MIN_INTERVAL_S)]

    def test_the_ticker_map(self):
        payload = json.dumps({"0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple Inc."}})
        client = SecClient(IDENTITY, opener=FakeOpener(payload.encode()), sleep=lambda s: None)
        assert client.tickers() == {"AAPL": (320193, "Apple Inc.")}

    @pytest.mark.parametrize("error, words", [
        (urllib.error.HTTPError("u", 403, "Forbidden", {}, None), "identity"),
        (urllib.error.HTTPError("u", 404, "Not Found", {}, None), "no XBRL facts"),
        (urllib.error.HTTPError("u", 500, "Oops", {}, None), "HTTP 500"),
        (urllib.error.URLError("no route"), "couldn't reach the SEC")])
    def test_failures_say_what_to_do(self, error, words):
        client = SecClient(IDENTITY, opener=FakeOpener(error=error), sleep=lambda s: None)
        with pytest.raises(SecError, match=words):
            client.companyfacts(1)

    @pytest.mark.parametrize("identity", ["", "jane@example.com", "Jane Doe"])
    def test_the_identity_names_a_person_and_an_email(self, identity):
        with pytest.raises(ValueError, match="identity"):
            SecClient(identity)


def test_facts_reject_what_isnt_a_fact():
    with pytest.raises(ValueError):
        Fact("us-gaap", "Revenues", "USD", None, "2025-09-27", math.nan, 2025, "FY", "10-K",
             "2025-10-31", "A")
    with pytest.raises(ValueError):
        Fact("us-gaap", "Revenues", "USD", None, "", 1.0, 2025, "FY", "10-K", "2025-10-31", "A")
