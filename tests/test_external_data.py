"""The external-data service (D60): fetch when stale, keep the copy on failure."""
from datetime import datetime, timedelta

import pytest

from quant_hub.providers.ken_french import DATASETS, FrenchError
from quant_hub.services.external_data import FRENCH_STALE_AFTER, ExternalDataService
from quant_hub.storage.series_store import SeriesStore
from tests.test_ken_french import PREAMBLE, ff3_file, zipped

T0 = datetime(2026, 11, 3, 21, 0)


def files():
    five = zipped(PREAMBLE + ",Mkt-RF,SMB,HML,RMW,CMA,RF\n"
                  "19630701, -0.67, 0.00, -0.33, -0.01, 0.16, 0.01\n\n")
    mom = zipped(PREAMBLE + ",Mom\n19261103, 0.55\n\n")
    return {"ff3": ff3_file(), "ff5": five, "mom": mom}


class FakeClient:
    def __init__(self, payloads, failing=()):
        self.payloads, self.failing, self.calls = payloads, set(failing), []

    def fetch(self, dataset):
        self.calls.append(dataset.key)
        if dataset.key in self.failing:
            raise FrenchError("couldn't reach Ken French's library: offline")
        return self.payloads[dataset.key]


@pytest.fixture
def setup():
    store = SeriesStore(":memory:")
    clock = {"now": T0}
    client = FakeClient(files())
    service = ExternalDataService(store, lambda: client, now=lambda: clock["now"])
    yield service, client, clock
    store.close()


def test_a_first_refresh_fetches_every_dataset_then_serves_them(setup):
    service, client, _ = setup
    outcomes = service.refresh_french()
    assert [(o.dataset, o.status) for o in outcomes] == [(k, "fetched") for k in DATASETS]
    assert outcomes[0].series == 4 and outcomes[0].values == 11     # 12 cells, one missing
    s = service.series("french", "ff5.rmw")
    assert list(s.values) == pytest.approx([-0.0001]) and s.meta.note == "CRSP 202609"
    assert {r.meta.series.split(".")[0] for r in service.catalog("french")} == set(DATASETS)


def test_a_fresh_copy_isnt_fetched_again_until_stale_or_forced(setup):
    service, client, clock = setup
    service.refresh_french()
    clock["now"] = T0 + FRENCH_STALE_AFTER
    assert {o.status for o in service.refresh_french()} == {"fresh"}
    assert len(client.calls) == 3
    clock["now"] = T0 + FRENCH_STALE_AFTER + timedelta(minutes=1)
    assert {o.status for o in service.refresh_french()} == {"fetched"}
    assert {o.status for o in service.refresh_french(force=True)} == {"fetched"}
    assert len(client.calls) == 9


def test_a_failed_dataset_keeps_its_stored_copy_and_the_others_still_fetch(setup):
    service, client, clock = setup
    service.refresh_french()
    client.failing = {"ff5"}
    outcomes = {o.dataset: o for o in service.refresh_french(force=True)}
    assert outcomes["ff5"].status == "failed" and "stored copy stays" in outcomes["ff5"].message
    assert outcomes["ff3"].status == outcomes["mom"].status == "fetched"
    assert service.series("french", "ff5.mkt_rf").fetched_at == T0


def test_a_series_never_fetched_says_how_to_get_it(setup):
    service, client, _ = setup
    client.failing = set(DATASETS)
    assert all(o.status == "failed" and "stays" not in o.message for o in service.refresh_french())
    with pytest.raises(LookupError, match="Fetch Ken French factors"):
        service.series("french", "ff3.mkt_rf")


def test_a_shiller_import_is_stored_whole_and_a_reimport_replaces_it(setup, tmp_path):
    from tests.test_shiller_csv import ROWS, sheet
    service, _, clock = setup
    path = tmp_path / "ie_data.csv"
    path.write_bytes(sheet(ROWS))
    imp = service.import_shiller(path)
    assert imp.dropped == "1871-12-01"
    first = service.series("shiller", "sp.price")
    assert list(first.values) == [4.5, 4.6, 4.7] and first.fetched_at == T0
    path.write_bytes(sheet(ROWS.replace("1871.09,4.50", "1871.09,4.55")))
    clock["now"] = T0 + timedelta(days=30)
    service.import_shiller(path)
    again = service.series("shiller", "sp.price")
    assert list(again.values) == [4.55, 4.6, 4.7] and again.sha256 != first.sha256
    with pytest.raises(LookupError, match="Import Shiller CSV"):
        service.series("shiller", "nope")
