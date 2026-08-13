"""Tests for the pure helper functions in the broker package.

These run whether or not ibapi is installed -- they exercise the request
formatting and bar normalization, not the network."""

from datetime import datetime

import pytest

from quant_hub.broker.ibkr import duration_string, parse_ib_date
from quant_hub.broker.market_source import bars_to_frame
from quant_hub.broker.types import HistoricalBar


class TestDurationString:
    def test_short_windows_in_days(self):
        assert duration_string(datetime(2024, 1, 1), datetime(2024, 1, 31)) == "31 D"

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

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            bars_to_frame([])
