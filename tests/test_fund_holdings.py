"""Fund holdings (D62): the store keeps every report date; the service checks
for a newer filing only when stale, and serves offline. No network."""
import json
from datetime import datetime, timedelta

import pytest

from quant_hub.providers.sec_edgar import SecError
from quant_hub.providers.sec_nport import FUND_TICKERS_URL, document_url, feed_url
from quant_hub.services.fund_holdings import STALE_AFTER, FundHoldingsService
from quant_hub.storage.holdings_store import HoldingsStore
from tests.test_sec_nport import FEED, nport_xml

T0 = datetime(2026, 11, 3, 21, 0)
MF = json.dumps({"fields": ["cik", "seriesId", "classId", "symbol"],
                 "data": [[999999, "S000000001", "C000000001", "XYZ"]]}).encode()


class FakeSec:
    def __init__(self):
        self.pages = {FUND_TICKERS_URL: MF,
                      feed_url("S000000001"): FEED,
                      feed_url(888888): FEED,
                      document_url(999999, "0000000000-26-000002"): nport_xml("S000000001"),
                      document_url(888888, "0000000000-26-000002"): nport_xml()}
        self.got, self.failing = [], False

    def get(self, url):
        self.got.append(url)
        if self.failing:
            raise SecError("couldn't reach the SEC: offline")
        return self.pages[url]

    def tickers(self):
        self.got.append("company tickers")
        return {"UIT": (888888, "Example Unit Trust")}


@pytest.fixture
def setup():
    store = HoldingsStore(":memory:")
    sec, clock = FakeSec(), {"now": T0}
    service = FundHoldingsService(store, "Tester tester@example.com", lambda _id: sec,
                                  now=lambda: clock["now"])
    yield service, store, sec, clock
    store.close()


def test_a_series_fund_resolves_through_the_fund_map_and_is_stored(setup):
    service, store, sec, _ = setup
    report = service.holdings("xyz")
    assert (report.fund, report.series_id, report.report_date) == ("XYZ", "S000000001", "2026-06-30")
    assert sec.got == [FUND_TICKERS_URL, feed_url("S000000001"),
                       document_url(999999, "0000000000-26-000002")]
    again, fetched = store.latest("XYZ")
    assert again == report and fetched == T0


def test_a_unit_trust_falls_back_to_the_company_map(setup):
    service, _, sec, _ = setup
    report = service.holdings("UIT")
    assert report.series_id == "" and "company tickers" in sec.got
    assert document_url(888888, "0000000000-26-000002") in sec.got


def test_a_fresh_report_needs_no_network_and_a_stale_one_no_document(setup):
    service, _, sec, clock = setup
    service.holdings("XYZ")
    sec.got.clear()
    clock["now"] = T0 + STALE_AFTER
    service.holdings("XYZ")
    assert sec.got == []
    clock["now"] = T0 + STALE_AFTER + timedelta(hours=1)
    service.holdings("XYZ")
    # The week-old ticker map refreshes too; the same accession means no document fetch.
    assert sec.got == [FUND_TICKERS_URL, feed_url("S000000001")]


def test_a_newer_filing_is_kept_beside_the_older_report(setup):
    service, store, sec, clock = setup
    service.holdings("XYZ")
    sec.pages[feed_url("S000000001")] = FEED.replace(b"26-000002", b"26-000003", 1)
    sec.pages[document_url(999999, "0000000000-26-000003")] = nport_xml("S000000001", date="2026-09-30")
    clock["now"] = T0 + timedelta(days=40)
    assert service.holdings("XYZ").report_date == "2026-09-30"
    assert service.report_dates("XYZ") == ["2026-06-30", "2026-09-30"]


def test_a_failed_check_serves_the_stored_report(setup):
    service, _, sec, clock = setup
    service.holdings("XYZ")
    sec.failing = True
    clock["now"] = T0 + timedelta(days=40)
    assert service.holdings("XYZ").report_date == "2026-06-30"
    with pytest.raises(SecError):
        service.holdings("UIT")                           # nothing stored to fall back on


def test_no_identity_and_an_unknown_ticker_say_what_to_do(setup):
    _, store, sec, _ = setup
    blind = FundHoldingsService(store, "", lambda _id: sec)
    with pytest.raises(SecError, match=r"\[sec\] identity"):
        blind.holdings("XYZ")
    service = setup[0]
    with pytest.raises(LookupError, match="no fund or company"):
        service.holdings("NOPE")


def test_the_store_replaces_a_report_date_and_keeps_others(setup):
    _, store, _, _ = setup
    from quant_hub.providers.sec_nport import parse_nport
    first = parse_nport(nport_xml(), "SPY", "a-1", "2026-08-28")
    assert store.save(first, T0, "h1") == 2
    amended = parse_nport(nport_xml(holdings=["<name>Only</name><pctVal>100</pctVal>"]),
                          "SPY", "a-2", "2026-09-01")
    store.save(amended, T0 + timedelta(days=1), "h2")
    report, fetched = store.latest("spy")
    assert [h.name for h in report.holdings] == ["Only"] and report.accession == "a-2"
    assert store.accession("SPY", "a-1") is None and store.accession("SPY", "a-2") == "2026-06-30"
