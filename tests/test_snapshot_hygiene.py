"""Snapshot hygiene (ALLOCATION A4): the same file imported twice is flagged
before it's stored, and under another layout says what a second copy would
do; and deleting a snapshot backs the store up first. Made-up exports."""

import pytest

from quant_hub.data.sources import SyntheticSource
from quant_hub.services.file_import import FileSource
from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import DuplicateImportError, PortfolioService, files_hash
from quant_hub.storage.position_store import PositionStore, PositionStoreError
from book_fixtures import bars, export


@pytest.fixture
def world(tmp_path):
    store = PositionStore(tmp_path / "market_data.db")
    store_of_bars, _ = bars()
    market = MarketService(sources=[SyntheticSource(), FileSource(store_of_bars)])
    portfolio = PortfolioService(market, store)
    older = tmp_path / "positions-older.csv"
    newer = tmp_path / "positions-newer.csv"
    older.write_text(export("10/15/2026", {"AAA": 40, "BBB": 10}, 4000), encoding="utf-8")
    newer.write_text(export("10/22/2026", {"AAA": 40, "BBB": 20, "CCC": 10}, 3000),
                     encoding="utf-8")
    yield portfolio, store, older, newer, tmp_path
    store.close()


def test_the_same_file_twice_is_flagged_before_its_stored(world):
    portfolio, store, older, _, _ = world
    first, _ = portfolio.import_positions(older)
    assert store.snapshots()[0].sha256 == files_hash([older])
    with pytest.raises(DuplicateImportError) as error:
        portfolio.import_positions(older)
    assert f"imported before as #{first}" in str(error.value)
    assert "still the newest snapshot" in str(error.value)
    assert not error.value.other_layout and len(store.snapshots()) == 1     # nothing stored
    second, _ = portfolio.import_positions(older, allow_duplicate=True)
    assert second != first and len(store.snapshots()) == 2


def test_under_another_layout_it_says_what_a_second_copy_would_do(world):
    portfolio, store, older, _, _ = world
    _, snapshot = portfolio.import_positions(older)
    store.save(snapshot.__class__(snapshot.positions, snapshot.as_of, "Fidelity", snapshot.name),
               sha256=files_hash([older]))                 # as an older importer read it
    with pytest.raises(DuplicateImportError) as error:
        portfolio.import_positions(older)
    assert error.value.other_layout
    assert "trades that never happened" in str(error.value)


def test_several_files_hash_the_same_in_any_order(world):
    _, _, older, newer, _ = world
    assert files_hash([older, newer]) == files_hash([newer, older])
    assert files_hash([older, newer]) not in {files_hash([older]), files_hash([newer])}


def test_a_delete_backs_the_store_up_first(world):
    portfolio, store, older, newer, tmp_path = world
    keep, _ = portfolio.import_positions(older)
    gone, _ = portfolio.import_positions(newer)
    backup = portfolio.delete_snapshot(gone)
    assert backup.is_file() and backup.parent == tmp_path / "backups"
    assert f"before-deleting-snapshot-{gone}" in backup.name
    assert [s.id for s in store.snapshots()] == [keep]
    with pytest.raises(PositionStoreError):
        store.load(gone)
    restored = PositionStore(backup)                        # the backup still holds it
    assert {s.id for s in restored.snapshots()} == {keep, gone}
    restored.close()
    with pytest.raises(PositionStoreError, match="no position snapshot"):
        portfolio.delete_snapshot(9999)

