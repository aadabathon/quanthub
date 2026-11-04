"""Ken French's daily factor files (D60): the parser and the client, offline."""
import io
import urllib.error
import zipfile

import pytest

from quant_hub.providers.ken_french import (
    BASE_URL,
    CITATION,
    DATASETS,
    USER_AGENT,
    FrenchClient,
    FrenchError,
    column_id,
    parse_dataset,
)

FF3 = DATASETS["ff3"]
MOM = DATASETS["mom"]
PREAMBLE = ("This file was created by using the 202609 CRSP database.\n"
            "The Tbill return is the simple daily rate that, over the number of trading days\n"
            "compounds to 1-month TBill rate.\n\n")
ROWS = ("19260701,    0.09,   -0.23,   -0.28,    0.01\n"
        "19260702,    0.45,  -99.99,   -0.03,    0.01\n"
        "19260706,    0.17,    0.29,   -0.38,    0.01\n")


def zipped(text, names=("F-F_Research_Data_Factors_daily.csv",)):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, text)
    return buffer.getvalue()


def ff3_file(rows=ROWS):
    return zipped(PREAMBLE + ",Mkt-RF,SMB,HML,RF\n" + rows
                  + "\nCopyright 2026 Eugene F. Fama and Kenneth R. French\n")


def test_each_column_becomes_a_series_of_fractions_with_its_provenance():
    parsed = {s.meta.series: s for s in parse_dataset(FF3, ff3_file())}
    assert sorted(parsed) == ["ff3.hml", "ff3.mkt_rf", "ff3.rf", "ff3.smb"]
    mkt = parsed["ff3.mkt_rf"]
    assert mkt.dates == ("1926-07-01", "1926-07-02", "1926-07-06")
    assert mkt.values == pytest.approx((0.0009, 0.0045, 0.0017))
    assert (mkt.meta.source, mkt.meta.unit, mkt.meta.frequency) == ("french", "fraction", "daily")
    assert mkt.meta.citation == CITATION and mkt.meta.note == "CRSP 202609"
    assert mkt.meta.title == "Fama/French 3 factors, daily: Mkt-RF"


def test_a_missing_value_is_left_out_of_its_own_column_only():
    parsed = {s.meta.series: s for s in parse_dataset(FF3, ff3_file())}
    assert parsed["ff3.smb"].dates == ("1926-07-01", "1926-07-06")
    assert len(parsed["ff3.hml"].dates) == 3


def test_the_header_is_found_after_a_longer_preamble():
    text = (PREAMBLE + "More notes.\nMissing data are indicated by -99.99 or -999.\n\n,Mom\n"
            "19261103,   0.55\n19261104,  -0.51\n\nCopyright\n")
    (mom,) = parse_dataset(MOM, zipped(text, ("F-F_Momentum_Factor_daily.csv",)))
    assert mom.meta.series == "mom.mom" and mom.values == pytest.approx((0.0055, -0.0051))


@pytest.mark.parametrize("payload, message", [
    (b"not a zip", "isn't a zip"),
    (zipped("x", ("a.csv", "b.csv")), "one CSV"),
    (zipped(PREAMBLE + "no header here\n"), "no header row"),
    (zipped(PREAMBLE + ",Mkt-RF,SMB\n19260701, 0.1\n"), "has 1 values"),
    (zipped(PREAMBLE + ",Mkt-RF\n19260702, 0.1\n19260701, 0.2\n"), "strictly increasing"),
    (zipped(PREAMBLE + ",Mkt-RF\n\nCopyright\n"), "no daily rows"),
    (zipped(PREAMBLE + ",Mkt-RF\n19261301, 0.1\n"), "can't read the row"),
])
def test_a_malformed_file_is_named_not_guessed(payload, message):
    with pytest.raises(FrenchError, match=message):
        parse_dataset(FF3, payload)


@pytest.mark.parametrize("name, expected", [("Mkt-RF", "mkt_rf"), ("Mom   ", "mom"),
                                            ("RF", "rf"), ("Lo 30", "lo_30")])
def test_column_ids(name, expected):
    assert column_id(name) == expected


class _Response:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_client_asks_once_with_a_plain_user_agent_and_a_timeout():
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, request.get_header("User-agent"), timeout))
        return _Response(b"zip bytes")
    assert FrenchClient(timeout_s=12, opener=opener).fetch(FF3) == b"zip bytes"
    assert seen == [(BASE_URL + FF3.file, USER_AGENT, 12)]


@pytest.mark.parametrize("error, message", [
    (urllib.error.HTTPError("u", 404, "gone", {}, None), "HTTP 404"),
    (urllib.error.URLError("no route"), "couldn't reach"),
    (TimeoutError("slow"), "couldn't reach"),
])
def test_network_failures_become_french_errors(error, message):
    def opener(request, timeout):
        raise error
    with pytest.raises(FrenchError, match=message):
        FrenchClient(opener=opener).fetch(FF3)
