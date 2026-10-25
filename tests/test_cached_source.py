"""Tests for the caching data source (quant_hub.services.cached_source).

A deterministic FakeSource stands in for IBKR: it records every fetch so
the tests can prove *exactly* which network calls the cache decisions
caused — including the milestone's core promise, "repeat request = zero
fetches". No TWS, no network; the store is in-memory or tmp_path.
"""

from datetime import datetime

import pandas as pd
import pytest

from quant_hub.data.ranges import DateRange
from quant_hub.data.sources import BasisUnavailableError, SourceOfflineError
from quant_hub.data.types import PriceHistory, Quote
from quant_hub.services.cached_source import CachedSource
from quant_hub.storage.bar_store import BarStore, InstrumentKey

# A fixed "today" long after the master data, so the yesterday-cap on
# coverage never interferes except in the tests that exercise it.
NOW = datetime(2024, 6, 15, 12, 0)


def master_frame() -> pd.DataFrame:
    """Deterministic daily bars on business days, Jan-Mar 2024."""
    index = pd.bdate_range("2024-01-01", "2024-03-29")
    closes = [100.0 + i * 0.5 for i in range(len(index))]
    return pd.DataFrame(
        {
            "open": [c - 0.25 for c in closes],
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1_000.0] * len(index),
        },
        index=index,
    )


class FakeSource:
    """BarSource returning slices of a master frame; records every fetch."""

    name = "fake"

    def __init__(self, frame: pd.DataFrame | None = None) -> None:
        self.frame = master_frame() if frame is None else frame
        self.fetch_calls: list[tuple[datetime, datetime]] = []
        self.fail_next = False
        self.offline = False

    def fetch_bars(self, symbol, start, end, bar_size) -> pd.DataFrame:
        if self.fail_next:
            raise ConnectionError("fake provider unavailable")
        if self.offline:
            raise SourceOfflineError("fake is disconnected")
        self.fetch_calls.append((start, end))
        return self.frame.loc[start:end]

    def get_quote(self, symbol) -> Quote:
        return Quote(symbol=symbol, price=123.0, timestamp=NOW, source=self.name)


@pytest.fixture
def store():
    s = BarStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def fake():
    return FakeSource()


@pytest.fixture
def cached(fake, store):
    return CachedSource(fake, store, bar_size="1 day", now=lambda: NOW)


class TestMissThenFullHit:
    def test_first_request_fetches_and_serves(self, cached, fake):
        history = cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert isinstance(history, PriceHistory)
        assert history.source == "fake"
        assert len(fake.fetch_calls) == 1
        # All January business days present with the master values.
        expected = master_frame().loc["2024-01-01":"2024-01-31"]
        assert len(history) == len(expected)
        assert history.closes.tolist() == expected["close"].tolist()

    def test_repeat_request_is_zero_fetches(self, cached, fake):
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        first_fetches = len(fake.fetch_calls)
        again = cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert len(fake.fetch_calls) == first_fetches  # FULL_HIT: no network
        assert len(again) == 23  # January 2024 business days

    def test_subrange_after_fetch_is_full_hit(self, cached, fake):
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 3, 29))
        fake.fetch_calls.clear()
        history = cached.get_history("SPY", datetime(2024, 2, 1), datetime(2024, 2, 15))
        assert fake.fetch_calls == []
        assert history.start == datetime(2024, 2, 1)


class TestPartialHit:
    def test_only_missing_tail_is_fetched(self, cached, fake):
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        fake.fetch_calls.clear()
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 2, 29))
        assert fake.fetch_calls == [(datetime(2024, 2, 1), datetime(2024, 2, 29))]

    def test_only_missing_head_is_fetched(self, cached, fake):
        cached.get_history("SPY", datetime(2024, 2, 1), datetime(2024, 2, 29))
        fake.fetch_calls.clear()
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 2, 29))
        assert fake.fetch_calls == [(datetime(2024, 1, 1), datetime(2024, 1, 31))]

    def test_interior_hole_fetched_alone(self, cached, fake):
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        cached.get_history("SPY", datetime(2024, 3, 1), datetime(2024, 3, 29))
        fake.fetch_calls.clear()
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 3, 29))
        assert fake.fetch_calls == [(datetime(2024, 2, 1), datetime(2024, 2, 29))]

    def test_weekend_tail_serves_cached_bars_then_full_hits(self, cached, fake):
        # 2024-03-29 is a Friday; ask through Sunday. The weekend fetch
        # legitimately returns nothing, the cached weekdays are served, and
        # the weekend becomes covered so the repeat is a FULL_HIT.
        cached.get_history("SPY", datetime(2024, 3, 1), datetime(2024, 3, 29))
        fake.fetch_calls.clear()
        history = cached.get_history("SPY", datetime(2024, 3, 1), datetime(2024, 3, 31))
        assert fake.fetch_calls == [(datetime(2024, 3, 30), datetime(2024, 3, 31))]
        assert history.end == datetime(2024, 3, 29)  # Friday is the last bar

        fake.fetch_calls.clear()
        cached.get_history("SPY", datetime(2024, 3, 1), datetime(2024, 3, 31))
        assert fake.fetch_calls == []  # weekend now covered: FULL_HIT


class TestFailuresAndEdges:
    @pytest.mark.parametrize("bar_size", ["1 week", "1 hour", "1 month"])
    def test_non_daily_bars_are_rejected(self, fake, store, bar_size):
        # Coverage runs through yesterday: right for daily bars only (F1).
        with pytest.raises(ValueError, match="daily bars only"):
            CachedSource(fake, store, bar_size=bar_size)

    def test_fetch_failure_propagates(self, cached, fake):
        fake.fail_next = True
        with pytest.raises(ConnectionError):
            cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))

    def test_failed_fetch_leaves_no_coverage(self, cached, fake, store):
        fake.fail_next = True
        with pytest.raises(ConnectionError):
            cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        fake.fail_next = False
        # Nothing was recorded, so the retry is a MISS that fetches fully.
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert fake.fetch_calls == [(datetime(2024, 1, 1), datetime(2024, 1, 31))]

    def test_empty_range_raises_explicitly(self, cached):
        # A weekend-only window: fetch succeeds, zero bars exist.
        with pytest.raises(ValueError, match="no 1 day bars"):
            cached.get_history("SPY", datetime(2024, 3, 30), datetime(2024, 3, 31))

    def test_malformed_provider_rows_not_persisted(self, store):
        frame = master_frame().loc["2024-01-01":"2024-01-10"]
        frame.loc[pd.Timestamp("2024-01-04"), "close"] = -1.0  # corrupt one row
        fake = FakeSource(frame)
        cached = CachedSource(fake, store, now=lambda: NOW)
        history = cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 10))
        assert pd.Timestamp("2024-01-04") not in history.frame.index
        assert len(history) == len(frame) - 1

    def test_todays_bar_refetched_not_frozen(self, store):
        # "Today" inside the master data: coverage must stop at yesterday,
        # so today's provisional bar is re-fetched on the next request.
        today = datetime(2024, 3, 15, 14, 30)
        fake = FakeSource()
        cached = CachedSource(fake, store, now=lambda: today)
        cached.get_history("SPY", datetime(2024, 3, 1), today)
        fake.fetch_calls.clear()
        cached.get_history("SPY", datetime(2024, 3, 1), today)
        assert fake.fetch_calls == [(datetime(2024, 3, 15), datetime(2024, 3, 15))]

    def test_quotes_pass_through_uncached(self, cached, fake):
        quote = cached.get_quote("SPY")
        assert quote.price == 123.0
        assert fake.fetch_calls == []

    def test_name_is_transparent(self, cached):
        assert cached.name == "fake"


class TestOffline:
    """V0: an offline provider serves what the store holds, labelled, but
    never across a hole (module docstring, Offline)."""

    def test_offline_tail_serves_the_stored_stretch(self, cached, fake):
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        fake.offline = True
        history = cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 2, 29))
        assert history.offline_reason == "fake is disconnected"
        assert history.end == datetime(2024, 1, 31)
        expected = master_frame().loc["2024-01-01":"2024-01-31", "close"]
        assert history.closes.tolist() == expected.tolist()

    def test_online_history_has_no_offline_reason(self, cached):
        assert cached.get_history("SPY", datetime(2024, 1, 1),
                                  datetime(2024, 1, 31)).offline_reason is None

    def test_offline_with_nothing_stored_fails(self, cached, fake):
        fake.offline = True
        with pytest.raises(SourceOfflineError, match="disconnected"):
            cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))

    def test_other_failures_still_fail_with_stored_bars(self, cached, fake):
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        fake.fail_next = True          # not "offline": a refused or broken request
        with pytest.raises(ConnectionError):
            cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 2, 29))

    def test_offline_never_serves_across_an_interior_hole(self, cached, fake):
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        cached.get_history("SPY", datetime(2024, 3, 1), datetime(2024, 3, 29))
        fake.offline = True
        with pytest.raises(SourceOfflineError, match="2024-01-31.*2024-03-01"):
            cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 3, 29))

    def test_a_weekend_between_stored_stretches_is_not_a_hole(self, cached, fake):
        # Coverage Jan 1-Fri Jan 12, then Mon Jan 15-31: no weekday between.
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 12))
        cached.get_history("SPY", datetime(2024, 1, 15), datetime(2024, 1, 31))
        fake.offline = True
        history = cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert history.offline_reason is not None
        assert len(history) == 23      # every January business day

    def test_offline_head_gap_serves_from_where_the_store_starts(self, cached, fake):
        cached.get_history("SPY", datetime(2024, 2, 1), datetime(2024, 2, 29))
        fake.offline = True
        history = cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 2, 29))
        assert history.start == datetime(2024, 2, 1)
        assert history.offline_reason is not None

    def test_offline_leaves_out_a_stored_provisional_bar(self, store):
        # A sync on Fri Mar 15 stores that day's provisional bar but covers
        # only through Mar 14; offline on Monday, Mar 15 must not be served.
        fake = FakeSource()
        friday = datetime(2024, 3, 15, 14, 30)
        CachedSource(fake, store, now=lambda: friday).get_history(
            "SPY", datetime(2024, 3, 1), friday)
        fake.offline = True
        monday = datetime(2024, 3, 18, 10, 0)
        history = CachedSource(fake, store, now=lambda: monday).get_history(
            "SPY", datetime(2024, 3, 1), monday)
        assert history.end == datetime(2024, 3, 14)


class TestPersistenceAcrossRestart:
    def test_new_process_full_hits_from_disk(self, tmp_path):
        """The milestone's relaunch scenario, as a deterministic test."""
        db = tmp_path / "bars.db"
        first_store = BarStore(db)
        first_fake = FakeSource()
        CachedSource(first_fake, first_store, now=lambda: NOW).get_history(
            "SPY", datetime(2024, 1, 1), datetime(2024, 2, 29)
        )
        first_store.close()

        second_store = BarStore(db)
        try:
            second_fake = FakeSource()
            history = CachedSource(second_fake, second_store, now=lambda: NOW).get_history(
                "SPY", datetime(2024, 1, 1), datetime(2024, 2, 29)
            )
            assert second_fake.fetch_calls == []  # served entirely from disk
            expected = master_frame().loc["2024-01-01":"2024-02-29", "close"]
            assert history.closes.tolist() == expected.tolist()
        finally:
            second_store.close()


# ----------------------------------------------------- adjusted prices (F3)
from datetime import timedelta  # noqa: E402

import numpy as np  # noqa: E402

from quant_hub.data.types import PriceBasis  # noqa: E402
from quant_hub.services.cached_source import dividend_steps_only  # noqa: E402


class IBKRLikeFake:
    """Traded and adjusted bars the way IBKR serves them (D38), as of the
    fake's own ``today``.

    * ``continuous`` is the price path on today's scale for every date.
    * A split (``split_on``, ``split_ratio``) leaves pre-split dates at
      ``ratio`` times that scale until ``today`` reaches the split date: from
      then on, IBKR serves all history split-adjusted.
    * Adjusted prices multiply every bar before each ex-date ``t`` (up to
      ``today``) by f_t = 1 - D_t / C_{t-1}, the traded close the day before.
    """

    name = "fake"

    def __init__(self, dividends, split_on=None, split_ratio=1.0):
        self.continuous = master_frame()
        self.dividends = {pd.Timestamp(day): amount for day, amount in dividends.items()}
        self.split_on = pd.Timestamp(split_on) if split_on else None
        self.split_ratio = split_ratio
        self.today = NOW
        self.calls: list[tuple[PriceBasis, datetime, datetime]] = []

    def traded(self) -> pd.DataFrame:
        frame = self.continuous.loc[: self.today].copy()
        if self.split_on is not None and self.today < self.split_on:
            prices = ["open", "high", "low", "close"]
            frame.loc[frame.index < self.split_on, prices] *= self.split_ratio
        return frame

    def adjusted(self) -> pd.DataFrame:
        traded = self.traded()
        factor = pd.Series(1.0, index=traded.index)
        for ex_date, amount in self.dividends.items():
            if ex_date > self.today or ex_date not in traded.index:
                continue
            previous_close = traded["close"].loc[: ex_date - timedelta(days=1)].iloc[-1]
            factor[traded.index < ex_date] *= 1 - amount / previous_close
        frame = traded.copy()
        for col in ("open", "high", "low", "close"):
            frame[col] *= factor
        return frame

    def fetch_bars(self, symbol, start, end, bar_size, basis=PriceBasis.TRADED):
        self.calls.append((basis, start, end))
        frame = self.adjusted() if basis is PriceBasis.ADJUSTED else self.traded()
        return frame.loc[start:end]

    def adjusted_calls(self):
        return [c for c in self.calls if c[0] is PriceBasis.ADJUSTED]


class TestAdjustedPrices:
    def make(self, fake, store, today):
        fake.today = today
        return CachedSource(fake, store, bar_size="1 day", now=lambda: today)

    def test_a_dividend_shows_as_total_return(self, store):
        fake = IBKRLikeFake({"2024-02-01": 1.5})
        cached = self.make(fake, store, datetime(2024, 3, 10))
        history = cached.get_history("SPY", datetime(2024, 1, 2), datetime(2024, 3, 8),
                                     PriceBasis.ADJUSTED)
        assert history.basis is PriceBasis.ADJUSTED
        a = history.closes
        p = fake.traded()["close"]
        ex, before = pd.Timestamp("2024-02-01"), pd.Timestamp("2024-01-31")
        # IBKR's convention: A_t / A_{t-1} = C_t / (C_{t-1} - D) on the ex-date,
        # the traded return with the dividend added back.
        assert a[ex] / a[before] == pytest.approx(p[ex] / (p[before] - 1.5), rel=1e-12)
        assert a[ex] / a[before] - 1 == pytest.approx((p[ex] + 1.5) / p[before] - 1, abs=1e-3)
        # Away from ex-dates, both bases give the same returns, and today's
        # prices are the same in each.
        assert a.iloc[-1] == pytest.approx(p.loc[a.index[-1]])
        day = pd.Timestamp("2024-02-15")
        assert a.pct_change()[day] == pytest.approx(p.pct_change()[day])

    def test_adjusted_is_refetched_whole_once_a_day(self, store):
        fake = IBKRLikeFake({"2024-02-01": 1.5, "2024-03-15": 2.0})
        window = (datetime(2024, 1, 2), datetime(2024, 3, 8))
        first = datetime(2024, 3, 10)
        self.make(fake, store, first).get_history("SPY", *window, PriceBasis.ADJUSTED)
        assert len(fake.adjusted_calls()) == 1            # the whole span, once
        self.make(fake, store, first).get_history("SPY", *window, PriceBasis.ADJUSTED)
        assert len(fake.adjusted_calls()) == 1            # same day: nothing new
        # Ten days on, a new ex-date has rescaled every earlier adjusted price.
        later = self.make(fake, store, datetime(2024, 3, 20))
        history = later.get_history("SPY", datetime(2024, 1, 2), datetime(2024, 3, 19),
                                    PriceBasis.ADJUSTED)
        assert len(fake.adjusted_calls()) == 2
        assert fake.adjusted_calls()[-1][1] == datetime(2024, 1, 2)   # whole span again
        expected = fake.adjusted()["close"].loc[history.closes.index]
        np.testing.assert_allclose(history.closes, expected, rtol=1e-12)   # no seam

    def test_a_split_after_caching_is_caught_and_the_traded_history_refetched(
            self, store, caplog):
        fake = IBKRLikeFake({"2024-02-01": 1.5}, split_on="2024-02-15", split_ratio=2.0)
        # Cached before the split: early bars at twice today's scale.
        self.make(fake, store, datetime(2024, 2, 10)).get_history(
            "SPY", datetime(2024, 1, 2), datetime(2024, 2, 9))
        after = self.make(fake, store, datetime(2024, 3, 10))
        traded = after.get_history("SPY", datetime(2024, 1, 2), datetime(2024, 3, 8))
        assert traded.closes.pct_change().min() < -0.4    # the fake -50% day, before the guard
        after.get_history("SPY", datetime(2024, 1, 2), datetime(2024, 3, 8), PriceBasis.ADJUSTED)
        assert "corporate action" in caplog.text
        repaired = after.get_history("SPY", datetime(2024, 1, 2), datetime(2024, 3, 8))
        np.testing.assert_allclose(
            repaired.closes, fake.traded()["close"].loc[repaired.closes.index], rtol=1e-12)
        assert repaired.closes.pct_change().abs().max() < 0.05

    def test_a_traded_sync_keeps_a_current_adjusted_series_whole(self, store):
        fake = IBKRLikeFake({"2024-02-01": 1.5})
        today = datetime(2024, 3, 10)
        self.make(fake, store, today).get_history(
            "SPY", datetime(2024, 2, 1), datetime(2024, 3, 8), PriceBasis.ADJUSTED)
        # Same day, a traded request reaches further back...
        self.make(fake, store, today).get_history(
            "SPY", datetime(2024, 1, 2), datetime(2024, 3, 8))
        # ...and the adjusted series reaches back with it.
        adjusted = self.make(fake, store, today).get_history(
            "SPY", datetime(2024, 1, 2), datetime(2024, 3, 8), PriceBasis.ADJUSTED)
        assert adjusted.start == datetime(2024, 1, 2)


class TestDividendStepGuard:
    INDEX = pd.bdate_range("2024-01-01", periods=6)
    TODAY = datetime(2024, 1, 20)

    def frames(self, ratio):
        traded = pd.DataFrame({"close": 100.0}, index=self.INDEX)
        return traded, traded.assign(close=100.0 * np.asarray(ratio))

    @pytest.mark.parametrize("ratio, ok", [
        ([0.98, 0.98, 0.99, 0.99, 1.0, 1.0], True),     # two dividends
        ([0.5, 0.5, 0.5, 1.0, 1.0, 1.0], False),        # a 2:1 split since caching
        ([1.0, 1.0, 0.9, 0.9, 0.9, 0.9], False),        # falling: a reverse split
        ([1.02, 1.02, 1.02, 1.02, 1.02, 1.02], False),  # adjusted above traded
    ])
    def test_steps(self, ratio, ok):
        assert dividend_steps_only(*self.frames(ratio), self.TODAY) is ok

    def test_cent_rounding_at_low_prices_is_not_a_split(self):
        # Found live: a split-adjusted history at low prices, refetched two
        # months apart, came back rounded differently by up to 0.04%.
        index = pd.bdate_range("2024-01-01", periods=6)
        traded = pd.DataFrame({"close": [13.01, 12.99, 13.02, 13.00, 12.98, 13.01]},
                              index=index)
        adjusted = traded.assign(close=[13.00, 13.00, 13.01, 13.01, 12.99, 13.00])
        assert dividend_steps_only(traded, adjusted, self.TODAY)
        # A 2:1 split at those prices is still caught.
        split = adjusted.assign(close=[6.50, 6.50, 6.51, 13.01, 12.99, 13.00])
        assert not dividend_steps_only(traded, split, self.TODAY)

    def test_todays_provisional_bar_is_ignored(self):
        traded, adjusted = self.frames([1.0] * 6)
        adjusted.iloc[-1, 0] = 140.0                     # moved since it was stored
        assert dividend_steps_only(traded, adjusted, self.INDEX[-1].to_pydatetime())


class TestAdjustedOffline:
    def test_adjusted_serves_through_the_day_before_its_refresh(self, store):
        fake = IBKRLikeFake({"2024-02-01": 1.5})
        refresh_day = datetime(2024, 3, 6, 15, 0)       # a Wednesday, mid-session
        fake.today = refresh_day
        CachedSource(fake, store, now=lambda: refresh_day).get_history(
            "SPY", datetime(2024, 1, 2), refresh_day, PriceBasis.ADJUSTED)

        def offline(*_args, **_kwargs):
            raise SourceOfflineError("not connected")
        fake.fetch_bars = offline
        later = datetime(2024, 3, 11, 9, 0)
        history = CachedSource(fake, store, now=lambda: later).get_history(
            "SPY", datetime(2024, 1, 2), later, PriceBasis.ADJUSTED)
        assert history.basis is PriceBasis.ADJUSTED
        assert history.offline_reason == "not connected"
        assert history.end == datetime(2024, 3, 5)     # Mar 6 was provisional

    def test_no_stored_adjusted_bars_is_basis_unavailable(self, store):
        fake = IBKRLikeFake({})
        cached = CachedSource(fake, store, now=lambda: NOW)
        cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31))   # traded only

        def offline(*_args, **_kwargs):
            raise SourceOfflineError("not connected")
        fake.fetch_bars = offline
        with pytest.raises(BasisUnavailableError, match="offline"):
            cached.get_history("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31),
                               PriceBasis.ADJUSTED)
