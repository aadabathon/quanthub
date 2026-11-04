"""Shiller's monthly data from the CSV you save (TARGET T1, D61): a made-up
fixture in the data sheet's layout; Shiller's own data is never committed."""
import pytest

from quant_hub.importers.shiller_csv import (
    CITATION,
    ShillerError,
    parse_shiller_csv,
    shiller_month,
)

TITLE = ('"Stock Market Data Used in \'Irrational Exuberance\'",,,,,,,,\n'
         '"Robert J. Shiller",,,,,,,,\n'
         ',"S&P",,,"Price",,"Long",,"P/E10 or"\n'
         ',"Comp.","Dividend","Earnings","Index","Date  ","Interest","Real","CAPE"\n')
HEADER = '"Date","P","D","E","CPI","Fraction","Rate GS10","Price","CAPE"\n'


def sheet(rows, header=HEADER, footer='"","Sep price is Sep 2nd close","","","CPI estimated"\n,,,,\n'):
    return (TITLE + header + rows + footer).encode("utf-8")


ROWS = ("1871.09,4.50,0.26,0.40,12.5,1871.71,5.30,120,NA\n"
        "1871.1,4.60,0.27,0.41,12.6,1871.79,5.31,121,NA\n"
        "1871.11,4.70,,,12.7,1871.88,5.32,122,20.5\n"
        "1871.12,4.80,,,12.8,1871.96,5.33,123,20.6\n")


@pytest.mark.parametrize("text, month", [("1871.1", (1871, 10)), ("1871.01", (1871, 1)),
                                         ("1871.10", (1871, 10)), ("2026.12", (2026, 12)),
                                         ("Date", None), ("", None), ("1871.13", None)])
def test_shiller_writes_october_as_point_one(text, month):
    assert shiller_month(text) == month


def test_the_sheet_becomes_monthly_series_with_the_last_month_dropped():
    imp = parse_shiller_csv(sheet(ROWS))
    by_id = {s.meta.series: s for s in imp.series}
    assert sorted(by_id) == ["cape", "cpi", "gs10", "sp.dividend", "sp.earnings", "sp.price"]
    assert by_id["sp.price"].dates == ("1871-09-01", "1871-10-01", "1871-11-01")
    assert by_id["sp.price"].values == (4.5, 4.6, 4.7)
    assert (imp.first, imp.last, imp.dropped) == ("1871-09-01", "1871-11-01", "1871-12-01")
    price = by_id["sp.price"].meta
    assert (price.source, price.frequency, price.unit, price.citation) == (
        "shiller", "monthly", "index", CITATION)
    assert (by_id["gs10"].meta.unit, by_id["cape"].meta.unit) == ("percent", "ratio")


def test_blank_and_na_cells_are_left_out_and_earnings_end_is_noted():
    imp = parse_shiller_csv(sheet(ROWS))
    by_id = {s.meta.series: s for s in imp.series}
    assert by_id["sp.earnings"].dates == ("1871-09-01", "1871-10-01")
    assert by_id["cape"].dates == ("1871-11-01",)               # NA earlier, last month dropped
    assert imp.earnings_through == "1871-10-01"
    assert imp.footnotes == "Sep price is Sep 2nd close; CPI estimated"
    assert by_id["cpi"].meta.note == "earnings through 1871-10; Sep price is Sep 2nd close; CPI estimated"


def test_columns_are_found_by_label_and_cape_is_optional():
    shuffled = '"Date","CPI","Rate GS10","E","D","P"\n'
    rows = "1871.09,12.5,5.3,0.40,0.26,4.5\n1871.1,12.6,5.31,0.41,0.27,4.6\n"
    by_id = {s.meta.series: s for s in parse_shiller_csv(sheet(rows, shuffled, "")).series}
    assert "cape" not in by_id and by_id["sp.price"].values == (4.5,)
    assert by_id["cpi"].values == (12.5,)


def test_quoted_thousands_and_a_windows_encoding_are_read():
    rows = '1871.09,"7,722.72",0.26,0.40,12.5,1871.71,5.30,120,NA\n1871.1,4.6,0.27,0.41,12.6,1871.79,5.31,121,NA\n'
    data = (TITLE + HEADER + rows).replace("Shiller", "Shiller – é").encode("cp1252")
    by_id = {s.meta.series: s for s in parse_shiller_csv(data).series}
    assert by_id["sp.price"].values == (7722.72,)


@pytest.mark.parametrize("payload, message", [
    (b"just,some,text\n1,2,3\n", "no header row"),
    (sheet(ROWS, '"Date","P","D","CPI","Rate GS10"\n'), "no 'E' column"),
    (sheet(ROWS, '"Date","P","P","D","E","CPI","Rate GS10"\n'), "names 'P' 2 times"),
    (sheet("1871.09,4.5,0.26,0.4,12.5,1,5.3,1,NA\n1871.11,4.6,0.27,0.41,12.6,1,5.31,1,NA\n"),
     "line 7: skips to 1871-11 after 1871-09"),
    (sheet("1871.09,4.5,0.26,0.4,12.5,1,5.3,1,NA\n1871.09,4.6,0.27,0.41,12.6,1,5.31,1,NA\n"),
     "line 7: repeats 1871-09"),
    (sheet("1871.09,4.5,0.26,0.4,12.5,1,5.3,1,NA\n"), "fewer than two months"),
])
def test_a_bad_sheet_names_what_and_where(payload, message):
    with pytest.raises(ShillerError, match=message):
        parse_shiller_csv(payload)
