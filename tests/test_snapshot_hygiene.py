"""Snapshot hygiene (ALLOCATION A4): the same file imported twice is flagged
before it's stored, and under another layout says what it would do to the
review; deleting a snapshot backs the store up first; and deleting the
phantom copy brings the review's trade drafts back to the real ones.
Made-up exports."""

from datetime import datetime

import pytest

from quant_hub.data.sources import SyntheticSource
from quant_hub.services.file_import import FileSource
from quant_hub.services.fundamentals import FundamentalsService
from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import DuplicateImportError, PortfolioService, files_hash
from quant_hub.services.review import ReviewService
from quant_hub.services.valuation import ValuationService
from quant_hub.storage.fundamentals_store import FundamentalsStore
from quant_hub.storage.position_store import PositionStore, PositionStoreError
from quant_hub.storage.regime_store import RegimeStore
from quant_hub.storage.valuation_store import ValuationStore
from test_review_service import NOW, RULES, bars, export


@pytest.fixture
def world(tmp_path):
    store = PositionStore(tmp_path / "market_data.db")
    store_of_bars, _ = bars()
    market = MarketService(sources=[SyntheticSource(), FileSource(store_of_bars)])
    portfolio = PortfolioService(market, store)
    older = tmp_path / "10-15-26_port.csv"
    newer = tmp_path / "10-22-26_port.csv"
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


def test_under_another_layout_it_says_what_the_review_would_do(world):
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


def test_deleting_the_phantom_copy_restores_the_reviews_drafts(world):
    portfolio, _, older, newer, tmp_path = world
    regime = tmp_path / "REGIME.md"
    regime.write_text(RULES, encoding="utf-8")
    valuation = ValuationService(FundamentalsService(FundamentalsStore(":memory:"), ""),
                                 portfolio._market, ValuationStore(":memory:"))
    review = ReviewService(portfolio, valuation, RegimeStore(":memory:"), regime,
                           now=lambda: NOW)
    portfolio.import_positions(older)
    portfolio.import_positions(newer)
    real = {d.symbol: d.change for d in review.run({"credit calm": True}).drafts}
    assert real == {"BBB": 10.0, "CCC": 10.0}
    with pytest.raises(DuplicateImportError, match="newest snapshot, so the review would read"):
        portfolio.import_positions(older)                   # flagged: an older file again
    phantom, _ = portfolio.import_positions(older, allow_duplicate=True)    # the mistake
    drafted = {d.symbol: d.change for d in review.run({"credit calm": True}).drafts}
    assert drafted == {"BBB": -10.0, "CCC": -10.0}                     # trades never made
    portfolio.delete_snapshot(phantom)
    assert {d.symbol: d.change for d in review.run({"credit calm": True}).drafts} == real
    assert datetime(2026, 10, 22).date() == review.run({"credit calm": True}).snapshot.as_of.date()
