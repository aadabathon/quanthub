"""Tests for the pure helper functions in the broker package.

These run whether or not ibapi is installed -- they exercise the request
formatting and bar normalization, not the network."""

from datetime import datetime

import pytest

from quant_hub.broker.ibkr import duration_string, parse_ib_date
from quant_hub.broker.market_source import IBKRSource, bars_to_frame
from quant_hub.broker.types import HistoricalBar


class TestDurationString:
    def test_short_windows_in_days(self):
        assert duration_string(datetime(2024, 1, 1), datetime(2024, 1, 31)) == "31 D"

    def test_single_day_window(self):
        # A one-session window happens on every same-day cache refresh.
        assert duration_string(datetime(2024, 1, 2), datetime(2024, 1, 2)) == "1 D"

    def test_year_boundary(self):
        assert duration_string(datetime(2023, 1, 2), datetime(2024, 1, 1)) == "365 D"

    def test_long_windows_in_years_rounded_up(self):
        assert duration_string(datetime(2019, 1, 1), datetime(2024, 1, 1)) == "5 Y"
        assert duration_string(datetime(2023, 1, 1), datetime(2024, 6, 1)) == "2 Y"

    def test_rejects_inverted_window(self):
        with pytest.raises(ValueError):
            duration_string(datetime(2024, 1, 2), datetime(2024, 1, 1))


class TestParseIbDate:
    def test_daily_format(self):
        assert parse_ib_date("20240102") == datetime(2024, 1, 2)

    def test_intraday_format(self):
        assert parse_ib_date("20240102 15:30:00") == datetime(2024, 1, 2, 15, 30)

    def test_intraday_with_timezone_suffix(self):
        assert parse_ib_date("20240102 15:30:00 US/Eastern") == datetime(
            2024, 1, 2, 15, 30
        )

    def test_garbage_rejected(self):
        with pytest.raises(ValueError):
            parse_ib_date("not-a-date")


class TestBarsToFrame:
    def test_conversion(self):
        bars = [
            HistoricalBar(datetime(2024, 1, 2), 10, 11, 9, 10.5, 1000),
            HistoricalBar(datetime(2024, 1, 3), 10.5, 12, 10, 11.5, 1200),
        ]
        frame = bars_to_frame(bars)
        assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
        assert len(frame) == 2
        assert frame["close"].iloc[1] == 11.5

    def test_empty_is_legal(self):
        # An empty window (weekend/holiday) is information, not an error;
        # the caching layer depends on receiving it as an empty frame.
        frame = bars_to_frame([])
        assert len(frame) == 0
        assert list(frame.columns) == ["open", "high", "low", "close", "volume"]


class FakeBroker:
    """Broker stub returning canned bars; records the requested bar size."""

    def __init__(self, bars):
        self.bars = bars
        self.bar_sizes = []

    def get_history(self, spec, start, end, bar_size="1 day"):
        self.bar_sizes.append(bar_size)
        return self.bars


class TestIBKRSourceFetchBars:
    BARS = [
        HistoricalBar(datetime(2024, 1, 2), 10, 11, 9, 10.5, 8_500),
        HistoricalBar(datetime(2024, 1, 3), 10.5, 12, 10, 11.5, 9_000),
        HistoricalBar(datetime(2024, 1, 4), 11.5, 13, 11, 12.5, 9_500),
    ]

    def test_volume_lots_converted_to_shares(self):
        # Live-TWS finding (2026-08-14): US-stock volume arrives in lots
        # of 100; the canonical frame stores shares.
        source = IBKRSource(FakeBroker(self.BARS))
        frame = source.fetch_bars("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31), "1 day")
        assert frame["volume"].tolist() == [850_000.0, 900_000.0, 950_000.0]

    def test_overfetch_sliced_to_window(self):
        source = IBKRSource(FakeBroker(self.BARS))
        frame = source.fetch_bars("SPY", datetime(2024, 1, 3), datetime(2024, 1, 4), "1 day")
        assert len(frame) == 2

    def test_bar_size_passed_through(self):
        broker = FakeBroker(self.BARS)
        IBKRSource(broker).fetch_bars("SPY", datetime(2024, 1, 1), datetime(2024, 1, 31), "1 week")
        assert broker.bar_sizes == ["1 week"]

    def test_empty_window_yields_empty_frame(self):
        source = IBKRSource(FakeBroker(self.BARS))
        frame = source.fetch_bars("SPY", datetime(2024, 2, 1), datetime(2024, 2, 2), "1 day")
        assert len(frame) == 0


class _RecordingApp:
    """Stands in for ibapi's client: records history requests and completes
    them at once (no bars), so ``IBKRBroker.get_history`` runs without TWS."""

    def __init__(self, broker):
        self.broker = broker
        self.requests = []

    def reqHistoricalData(self, **kwargs):  # noqa: N802 (ibapi API)
        self.requests.append(kwargs)
        self.broker._on_request_done(kwargs["reqId"])

    def cancelHistoricalData(self, req_id):  # noqa: N802 (ibapi API)
        pass


class TestHistoryRequestReachesStart:
    def test_window_ending_in_the_past_is_measured_to_now(self):
        # Live-TWS finding (2026-10-15): requests end "now" (D11), so a
        # cached series' leading gap [start, end] with end in the past must
        # ask for start..now. Asking for its own length (start..end) came
        # back anchored at now, lost its first year, and the cache recorded
        # the whole gap as covered.
        pytest.importorskip("ibapi")
        from datetime import timedelta

        from quant_hub.broker.base import ConnectionState
        from quant_hub.broker.ibkr import IBKRBroker
        from quant_hub.broker.types import ContractSpec
        from quant_hub.config import AppConfig
        from quant_hub.events import EventBus

        broker = IBKRBroker(AppConfig(), EventBus())
        broker._state = ConnectionState.CONNECTED
        broker._app = _RecordingApp(broker)
        now = datetime.now()
        start, end = now - timedelta(days=5 * 365), now - timedelta(days=365)
        broker.get_history(ContractSpec("SPY"), start, end)
        assert broker._app.requests[0]["endDateTime"] == ""
        assert broker._app.requests[0]["durationStr"] == "5 Y"    # not "4 Y"
