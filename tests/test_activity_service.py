"""Importing activity (TR1): every file read first, then all stored or none."""

from datetime import date

import pytest

from quant_hub.services.activity import ActivityImportError, ActivityService
from quant_hub.storage.activity_store import ActivityStore

HEADER = "date,account,kind,symbol,quantity,price,commission,fees,amount\n"
MARCH = HEADER + ("2026-03-02,Brokerage,buy,AAA,10,10.00,0,0,-100.00\n"
                  "2026-03-20,Brokerage,sell,AAA,-10,12.00,0,0,120.00\n")
MARCH_APRIL = MARCH + "2026-04-01,Brokerage,deposit,,,,,,50.00\n"


@pytest.fixture
def service():
    store = ActivityStore(":memory:")
    yield ActivityService(store)
    store.close()


def write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_file_is_stored_and_read_back(service, tmp_path):
    (result,) = service.import_activity(write(tmp_path, "march.csv", MARCH))
    assert (result.summary.rows, result.summary.added, result.already_stored,
            result.seen_before) == (2, 2, 0, False)
    assert [t.amount for t in service.transactions()] == [-100.0, 120.0]


def test_an_overlapping_export_adds_only_what_is_new(service, tmp_path):
    service.import_activity(write(tmp_path, "march.csv", MARCH))
    (result,) = service.import_activity(write(tmp_path, "march_april.csv", MARCH_APRIL))
    assert (result.summary.added, result.already_stored, result.seen_before) == (1, 2, False)
    assert len(service.transactions()) == 3
    assert service.transactions(start=date(2026, 4, 1))[0].amount == 50.0


def test_the_same_file_again_is_recognized_and_adds_nothing(service, tmp_path):
    path = write(tmp_path, "march.csv", MARCH)
    service.import_activity(path)
    (result,) = service.import_activity(path)
    assert (result.summary.added, result.seen_before) == (0, True)
    assert [i.name for i in service.imports()] == ["march.csv", "march.csv"]


def test_one_bad_row_anywhere_stores_nothing_and_lists_every_problem(service, tmp_path):
    good = write(tmp_path, "good.csv", MARCH)
    bad = write(tmp_path, "bad.csv", HEADER + "2026-03-02,Brokerage,buy,AAA,10,10.00,0,0,-5\n"
                                              "2026-03-03,Brokerage,deposit,,,,,,-1\n")
    with pytest.raises(ActivityImportError) as caught:
        service.import_activity([good, bad])
    assert len(caught.value.problems) == 2
    assert caught.value.problems[0].startswith("bad.csv line 2:")
    assert caught.value.problems[1].startswith("bad.csv line 3:")
    assert "2 problems" in str(caught.value)
    assert service.transactions() == [] and service.imports() == []


def test_an_unreadable_file_stores_nothing(service, tmp_path):
    good = write(tmp_path, "good.csv", MARCH)
    with pytest.raises(ActivityImportError, match="absent.csv"):
        service.import_activity([good, tmp_path / "absent.csv"])
    assert service.transactions() == []


def test_several_files_at_once(service, tmp_path):
    results = service.import_activity([write(tmp_path, "march.csv", MARCH),
                                       write(tmp_path, "march_april.csv", MARCH_APRIL)])
    assert [r.summary.added for r in results] == [2, 1]


def test_no_files_is_an_error(service):
    with pytest.raises(ActivityImportError, match="no files"):
        service.import_activity([])


def test_a_fidelity_download_names_the_wording_it_kept_as_other(service, tmp_path):
    from tests.test_activity_csv import FIDELITY
    (result,) = service.import_activity(write(tmp_path, "history.csv", FIDELITY.lstrip("﻿")))
    assert (result.summary.source, result.summary.added) == ("Fidelity", 7)
    assert result.unrecognized[1] == "Electronic Funds Transfer Received (Cash)"
    assert len(result.unrecognized) == 3
