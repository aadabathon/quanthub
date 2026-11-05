"""FRED (HEDGE U2, D69): observations parsed from JSON shaped like FRED's, with
"." days left out and the credit line in each citation; the key checked,
never echoed in an error; and the service's refresh: stale or forced, a
failure keeping the stored copy, no key saying how to set one. Made-up
values throughout."""

import io
import json
import urllib.error
from datetime import datetime, timedelta

import pytest

from quant_hub.providers.fred import (
    CATALOG,
    CREDIT,
    FredClient,
    FredError,
    observations_url,
    parse_observations,
    valid_key,
)
from quant_hub.services.external_data import (
    FRED_STALE_AFTER,
    NO_FRED_KEY,
    ExternalDataService,
)
from quant_hub.storage.series_store import SeriesStore

KEY = "0123456789abcdef0123456789abcdef"
T0 = datetime(2026, 11, 4, 18, 0)


def payload(rows):
    """FRED's observations JSON, with made-up rows (date, value text)."""
    return json.dumps({"realtime_start": "2026-11-04", "units": "lin", "count": len(rows),
                       "observations": [{"realtime_start": "2026-11-04",
                                         "realtime_end": "2026-11-04", "date": d, "value": v}
                                        for d, v in rows]}).encode()


VIX = payload([("1990-01-02", "17.24"), ("1990-01-03", "18.19"), ("1990-01-04", "."),
               ("1990-01-05", "19.22")])


# --------------------------------------------------------------- provider

def test_observations_skip_the_missing_days_and_carry_the_credit_line():
    vix = parse_observations(CATALOG["VIXCLS"], VIX)
    assert vix.dates == ("1990-01-02", "1990-01-03", "1990-01-05")
    assert vix.values == (17.24, 18.19, 19.22)
    meta = vix.meta
    assert (meta.source, meta.series, meta.unit, meta.frequency) == (
        "fred", "vixcls", "percent", "daily")
    assert meta.citation.startswith(CREDIT) and "Chicago Board Options Exchange" in meta.citation
    assert parse_observations(CATALOG["DTB3"], payload([("1954-01-04", "1.33")])).meta.citation == CREDIT


@pytest.mark.parametrize("body, message", [
    (b"<html>not json</html>", "isn't its observations JSON"),
    (json.dumps({"seriess": []}).encode(), "isn't its observations JSON"),
    (payload([("01/02/1990", "17.24")]), "isn't a date"),
    (payload([("1990-01-02", "n/a")]), "isn't a number"),
    (payload([("1990-01-02", ".")]), "no values"),
])
def test_a_reply_that_cant_be_read_says_what_is_wrong(body, message):
    with pytest.raises(FredError, match=message):
        parse_observations(CATALOG["VIXCLS"], body)


def test_the_key_is_checked_and_never_appears_in_an_error():
    assert valid_key(KEY) and not valid_key(KEY.upper()) and not valid_key("short")
    with pytest.raises(FredError, match=r"\[fred\] key"):
        FredClient("not a key")
    assert f"api_key={KEY}" in observations_url("VIXCLS", KEY)

    def refused(request, timeout):
        body = io.BytesIO(json.dumps({"error_code": 400, "error_message":
                                      "Bad Request. The value for variable api_key is not "
                                      "registered."}).encode())
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, body)
    with pytest.raises(FredError) as error:
        FredClient(KEY, opener=refused).observations(CATALOG["VIXCLS"])
    assert "HTTP 400 for VIXCLS" in str(error.value) and "not registered" in str(error.value)
    assert KEY not in str(error.value)

    def offline(request, timeout):
        raise urllib.error.URLError("no route")
    with pytest.raises(FredError, match="couldn't reach FRED: no route") as error:
        FredClient(KEY, opener=offline).observations(CATALOG["DTB3"])
    assert KEY not in str(error.value)


# ---------------------------------------------------------------- service

class FakeFred:
    def __init__(self, failing=()):
        self.asked, self.failing = [], set(failing)

    def observations(self, series):
        self.asked.append(series.fred_id)
        if series.fred_id in self.failing:
            raise FredError("couldn't reach FRED: offline")
        return payload([("2026-11-02", "4.10"), ("2026-11-03", "4.12")])


@pytest.fixture
def setup():
    store = SeriesStore(":memory:")
    clock, fake = {"now": T0}, FakeFred()
    keys = []

    def factory(key):
        keys.append(key)
        return fake
    service = ExternalDataService(store, now=lambda: clock["now"], fred_key=KEY,
                                  fred_client_factory=factory)
    yield service, store, fake, clock, keys
    store.close()


def test_a_refresh_fetches_every_series_then_waits_until_stale(setup):
    service, store, fake, clock, keys = setup
    outcomes = service.refresh_fred()
    assert [(o.dataset, o.status) for o in outcomes] == [
        ("vixcls", "fetched"), ("dtb3", "fetched"), ("dgs3mo", "fetched")]
    assert keys == [KEY] and fake.asked == ["VIXCLS", "DTB3", "DGS3MO"]
    assert service.series("fred", "vixcls").values.iloc[-1] == 4.12
    fake.asked.clear()
    clock["now"] = T0 + FRED_STALE_AFTER
    assert {o.status for o in service.refresh_fred()} == {"fresh"} and fake.asked == []
    assert {o.status for o in service.refresh_fred(force=True)} == {"fetched"}


def test_a_failed_series_keeps_its_stored_copy(setup):
    service, store, fake, clock, _ = setup
    service.refresh_fred()
    fake.failing = {"DTB3"}
    clock["now"] = T0 + timedelta(days=2)
    outcomes = {o.dataset: o for o in service.refresh_fred()}
    assert outcomes["dtb3"].status == "failed" and "stored copy stays" in outcomes["dtb3"].message
    assert outcomes["vixcls"].status == "fetched"
    assert service.series("fred", "dtb3").values.iloc[-1] == 4.12


def test_without_a_key_it_says_how_to_set_one(setup):
    _, store, *_ = setup
    blind = ExternalDataService(store)
    assert not blind.has_fred_key
    with pytest.raises(FredError) as error:
        blind.refresh_fred()
    assert str(error.value) == NO_FRED_KEY
    with pytest.raises(LookupError, match="Fetch FRED series"):
        blind.series("fred", "vixcls")
