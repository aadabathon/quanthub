"""The activity store (TR1): every transaction once, however files overlap."""

import random
from collections import Counter
from datetime import date, datetime, timedelta

import pytest

from quant_hub.data.activity import ActivityFile, Kind, Transaction
from quant_hub.data.positions import OptionContract
from quant_hub.storage.activity_store import ActivityStore, ActivityStoreError

START = date(2026, 3, 2)


def buy(day: date = START, quantity: float = 1.0, price: float = 100.0) -> Transaction:
    return Transaction(day, Kind.BUY, -quantity * price, account="Brokerage", symbol="AAA",
                       quantity=quantity, price=price)


def file(*transactions: Transaction, name: str = "history.csv") -> ActivityFile:
    return ActivityFile(tuple(transactions), "Generic", name)


@pytest.fixture
def store():
    s = ActivityStore(":memory:")
    yield s
    s.close()


def test_a_transaction_comes_back_as_it_went_in(store):
    call = OptionContract("AAA", "call", 50.0, date(2026, 6, 19))
    rows = (Transaction(START, Kind.BUY, -831.34, account="Brokerage", symbol="AAA260619C50",
                        quantity=2.0, price=4.15, commission=1.30, fees=0.04,
                        settle_date=START + timedelta(days=1), description="opening a call",
                        option=call),
            Transaction(START, Kind.DEPOSIT, 500.0, account="Brokerage"))
    store.save(file(*rows))
    assert tuple(store.transactions()) == rows


def test_two_identical_rows_in_one_file_are_both_kept(store):
    summary = store.save(file(buy(), buy()))
    assert (summary.rows, summary.added) == (2, 2)
    assert store.transactions() == [buy(), buy()]


def test_the_same_file_again_adds_nothing(store):
    store.save(file(buy(), buy(), buy(quantity=2.0)))
    again = store.save(file(buy(), buy(), buy(quantity=2.0)))
    assert (again.rows, again.added) == (3, 0) and len(store.transactions()) == 3


@pytest.mark.parametrize("first, second, kept", [(2, 1, 2), (1, 3, 3), (2, 2, 2), (0, 2, 2)])
def test_the_store_keeps_the_most_copies_any_one_file_held(store, first, second, kept):
    other = buy(quantity=5.0)                     # so no file is empty
    store.save(file(other, *[buy()] * first))
    store.save(file(other, *[buy()] * second))
    assert Counter(store.transactions())[buy()] == kept


def test_overlapping_exports_rebuild_the_true_history(store):
    """However two complete exports overlap, and in either order, the store
    ends up with each transaction exactly as often as it happened."""
    rng = random.Random(20261123)
    days = [START + timedelta(days=i) for i in range(30)]
    for trial in range(40):
        s = ActivityStore(":memory:")
        # The true history: a few distinct trades a day, some repeated.
        truth = [buy(day, quantity=rng.choice([1.0, 2.0]), price=rng.choice([10.0, 11.0]))
                 for day in days for _ in range(rng.randint(0, 4))]
        a, b = sorted(rng.sample(range(1, 29), 2))
        spans = [(0, b), (a, 29)]                 # overlapping, together all 30 days
        rng.shuffle(spans)
        for lo, hi in spans:
            rows = [t for t in truth if days[lo] <= t.trade_date <= days[hi]]
            rng.shuffle(rows)                     # any order within a file
            if rows:
                s.save(file(*rows))
        assert Counter(s.transactions()) == Counter(truth), trial
        s.close()


def test_rows_come_back_by_date_then_in_file_order(store):
    later, earlier = buy(START + timedelta(days=1)), buy(START, quantity=3.0)
    store.save(file(later, buy(quantity=2.0), earlier))
    assert store.transactions() == [buy(quantity=2.0), earlier, later]


def test_rows_can_be_read_by_account_and_dates(store):
    other = Transaction(START, Kind.DEPOSIT, 10.0, account="IRA")
    store.save(file(buy(), buy(START + timedelta(days=5)), other))
    assert store.transactions(account="IRA") == [other]
    assert store.transactions(start=START + timedelta(days=1)) == [buy(START + timedelta(days=5))]
    assert len(store.transactions(end=START)) == 2


def test_imports_are_listed_newest_first_with_what_they_added(store):
    store.save(file(buy(), name="march.csv"), "aa", datetime(2026, 3, 31, 9, 0))
    store.save(file(buy(), buy(START + timedelta(days=40)), name="april.csv"), "bb",
               datetime(2026, 4, 30, 9, 0))
    newest, oldest = store.imports()
    assert (newest.name, newest.rows, newest.added, newest.last_date) == (
        "april.csv", 2, 1, START + timedelta(days=40))
    assert (oldest.name, oldest.sha256, oldest.imported_at) == (
        "march.csv", "aa", datetime(2026, 3, 31, 9, 0))
    assert [s.name for s in store.with_hash("aa")] == ["march.csv"]
    assert store.with_hash("") == []


def test_an_empty_file_is_refused(store):
    with pytest.raises(ActivityStoreError, match="no transactions"):
        store.save(file())


def test_the_store_shares_a_file_with_other_tables(tmp_path):
    db = tmp_path / "market_data.db"
    first = ActivityStore(db)
    first.save(file(buy()))
    first.close()
    again = ActivityStore(db)
    assert again.transactions() == [buy()]
    again.close()
