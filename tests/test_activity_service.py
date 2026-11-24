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
    assert result.unrecognized[1] == "YOU BOUGHT US TREASURY NOTE (91282CAB7) (Cash)"
    assert len(result.unrecognized) == 2


def test_reconcile_uses_file_snapshots_one_per_day(tmp_path):
    from datetime import datetime

    from quant_hub.data.positions import Holding, Position, PositionSnapshot
    from quant_hub.storage.position_store import PositionStore

    def snapshot(day: int, shares: float, name: str) -> PositionSnapshot:
        return PositionSnapshot((Position("AAA", Holding.EQUITY, shares, "Brokerage"),),
                                datetime(2026, 3, day), "Generic", name)

    store, positions = ActivityStore(":memory:"), PositionStore(":memory:")
    service = ActivityService(store, positions)
    service.import_activity(write(tmp_path, "march.csv", MARCH))     # +10 on the 2nd, -10 on the 20th
    positions.save(snapshot(10, 10.0, "old.csv"), datetime(2026, 3, 10, 9), "aa")
    positions.save(snapshot(10, 99.0, "wrong.csv"), datetime(2026, 3, 10, 8), "bb")  # older import
    positions.save(snapshot(25, 0.0, "late.csv"), datetime(2026, 3, 25, 9), "cc")
    positions.save(snapshot(26, 7.0, "live.csv"), datetime(2026, 3, 26, 9), "")      # not a file
    result = service.reconcile()
    assert result.reconciled and [i.end_name for i in result.intervals] == ["late.csv"]
    assert result.intervals[0].start_name == "old.csv"
    with pytest.raises(ValueError, match="no positions store"):
        ActivityService(store).reconcile()
    store.close(), positions.close()


def test_round_trips_and_basis_from_the_stored_history(tmp_path):
    from datetime import datetime

    from quant_hub.data.positions import Holding, Position, PositionSnapshot
    from quant_hub.storage.position_store import PositionStore

    store, positions = ActivityStore(":memory:"), PositionStore(":memory:")
    service = ActivityService(store, positions)
    service.import_activity(write(tmp_path, "march.csv", MARCH))     # +10 at 10, -10 at 12
    (trip,) = service.round_trips()
    assert (trip.side, trip.closed is not None, trip.realized) == ("long", True, 20.0)
    held = PositionSnapshot((Position("AAA", Holding.EQUITY, 10.0, "Brokerage", cost_basis=100.0),),
                            datetime(2026, 3, 10), "Generic", "mid.csv")
    positions.save(held, datetime(2026, 3, 10, 9), "aa")
    (check,) = service.basis_checks()
    assert check.fifo_matches and check.snapshot == "mid.csv"
    store.close(), positions.close()
