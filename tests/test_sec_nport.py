"""SEC Form N-PORT (D62): the fund ticker map, the filing feed and the report,
parsed offline from fixtures shaped like the real documents (made-up holdings)."""
import json

import pytest

from quant_hub.providers.sec_nport import (
    NportError,
    document_url,
    feed_url,
    parse_filing_feed,
    parse_fund_tickers,
    parse_nport,
)


def nport_xml(series="", holdings=None, net="1000000.00", date="2026-06-30"):
    lines = holdings if holdings is not None else [
        ('<name>Alpha Corp</name><lei>LEI0000000000000000A1</lei><title>Alpha Corp</title>'
         '<cusip>000000AA1</cusip><identifiers><isin value="US000000AA10"/></identifiers>'
         '<balance>100.0</balance><units>NS</units><curCd>USD</curCd><valUSD>60000.0</valUSD>'
         '<pctVal>6.0</pctVal><payoffProfile>Long</payoffProfile><assetCat>EC</assetCat>'
         '<issuerCat>CORP</issuerCat><invCountry>US</invCountry>'),
        ('<name>Beta Inc</name><lei>N/A</lei><cusip>000000BB2</cusip>'
         '<identifiers><ticker value="bbb"/></identifiers><balance>-5</balance><units>NS</units>'
         '<valUSD>N/A</valUSD><pctVal>-0.5</pctVal><payoffProfile>Short</payoffProfile>'
         '<assetCat>EC</assetCat><issuerCat>CORP</issuerCat><invCountry>CA</invCountry>'),
    ]
    series_tag = f"<seriesId>{series}</seriesId>" if series else "<seriesLei>LEI</seriesLei>"
    secs = "".join(f"<invstOrSec>{h}</invstOrSec>" for h in lines)
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<edgarSubmission xmlns="http://www.sec.gov/edgar/nport" '
            f'xmlns:com="http://www.sec.gov/edgar/common">'
            f'<headerData><filerInfo><filer><issuerCredentials><cik>0000999999</cik>'
            f'</issuerCredentials></filer></filerInfo></headerData>'
            f'<formData><genInfo><regName>Example ETF Trust</regName><regCik>0000999999</regCik>'
            f'<seriesName>{"Example Series" if series else "N/A"}</seriesName>{series_tag}'
            f'<repPdEnd>2026-09-30</repPdEnd><repPdDate>{date}</repPdDate></genInfo>'
            f'<fundInfo><totAssets>1100000</totAssets><netAssets>{net}</netAssets></fundInfo>'
            f'<invstOrSecs>{secs}</invstOrSecs></formData></edgarSubmission>').encode("utf-8")


FEED = (b'<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">'
        b'<company-info><cik>0000999999</cik><conformed-name>EXAMPLE ETF TRUST</conformed-name>'
        b'</company-info>'
        b'<entry><category label="form type" term="NPORT-P"/><content type="text/xml">'
        b'<accession-number>0000000000-26-000002</accession-number><filing-date>2026-08-28'
        b'</filing-date><filing-type>NPORT-P</filing-type></content></entry>'
        b'<entry><content type="text/xml"><accession-number>0000000000-26-000001'
        b'</accession-number><filing-date>2026-05-28</filing-date><filing-type>NPORT-P/A'
        b'</filing-type></content></entry>'
        b'<entry><content type="text/xml"><accession-number>0000000000-26-000000'
        b'</accession-number><filing-date>2026-05-01</filing-date><filing-type>N-CEN'
        b'</filing-type></content></entry></feed>')


def test_a_unit_trust_report_becomes_holdings_with_fractions_and_identifiers():
    report = parse_nport(nport_xml(), "spy", "0000000000-26-000002", "2026-08-28")
    assert (report.fund, report.cik, report.series_id) == ("SPY", 999999, "")
    assert (report.fund_name, report.report_date, report.net_assets) == (
        "Example ETF Trust", "2026-06-30", 1_000_000.0)
    alpha, beta = report.holdings
    assert (alpha.name, alpha.lei, alpha.cusip, alpha.isin) == (
        "Alpha Corp", "LEI0000000000000000A1", "000000AA1", "US000000AA10")
    assert (alpha.pct, alpha.value_usd, alpha.units, alpha.payoff) == (0.06, 60000.0, "NS", "Long")
    assert (beta.lei, beta.ticker, beta.value_usd, beta.balance, beta.pct) == ("", "BBB", None, -5.0, -0.005)
    assert report.weight_covered == pytest.approx(0.055)


def test_a_series_trust_report_names_its_series_and_a_wrong_one_is_refused():
    report = parse_nport(nport_xml("S000000001"), "XYZ", "acc", "2026-08-28", "S000000001")
    assert (report.series_id, report.fund_name) == ("S000000001", "Example Series")
    with pytest.raises(NportError, match="reports series S000000001, not S000000002"):
        parse_nport(nport_xml("S000000001"), "XYZ", "acc", "2026-08-28", "S000000002")


@pytest.mark.parametrize("payload, message", [
    (b"<not-xml", "well-formed"),
    (b"<edgarSubmission/>", "no formData/genInfo"),
    (nport_xml(holdings=[]), "lists no holdings"),
    (nport_xml(date="06/30/2026"), "not an ISO date"),
])
def test_an_unreadable_report_is_named(payload, message):
    with pytest.raises(NportError, match=message):
        parse_nport(payload, "SPY", "acc", "2026-08-28")


def test_the_feed_lists_nport_filings_newest_first_amendments_included():
    refs = parse_filing_feed(FEED)
    assert [(r.accession, r.filed, r.form) for r in refs] == [
        ("0000000000-26-000002", "2026-08-28", "NPORT-P"),
        ("0000000000-26-000001", "2026-05-28", "NPORT-P/A")]


def test_the_fund_ticker_map_is_read_by_field_name():
    doc = {"fields": ["symbol", "cik", "classId", "seriesId"],
           "data": [["xyz", 111, "C000000001", "S000000001"], ["", 222, "C2", "S2"]]}
    assert parse_fund_tickers(json.dumps(doc).encode()) == {"XYZ": (111, "S000000001", "C000000001")}
    with pytest.raises(NportError, match="unexpected shape"):
        parse_fund_tickers(b'{"fields": ["cik"], "data": []}')


def test_urls():
    assert "CIK=S000000001&type=NPORT-P" in feed_url("S000000001")
    assert "CIK=0000884394&" in feed_url(884394)
    assert document_url(884394, "0001410368-26-089410") == (
        "https://www.sec.gov/Archives/edgar/data/884394/000141036826089410/primary_doc.xml")
