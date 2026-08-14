"""Tests for canonical OHLCV validation (quant_hub.data.bars).

Structural failures must raise; row-level failures must drop exactly the
offending rows and report every one of them; the repair path (negative
volume) must keep the row. No network, no database.
"""

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.bars import (
    MalformedBarsError,
    ValidationReport,
    validate_ohlcv_frame,
)


def make_frame(**overrides) -> pd.DataFrame:
    """A small clean OHLCV frame; columns overridable per test."""
    index = pd.DatetimeIndex(
        [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04")]
    )
    data = {
        "open": [100.0, 101.0, 102.0],
        "high": [101.5, 102.5, 103.5],
        "low": [99.5, 100.5, 101.5],
        "close": [101.0, 102.0, 103.0],
        "volume": [1000.0, 1100.0, 1200.0],
    }
    data.update(overrides)
    return pd.DataFrame(data, index=index)


class TestStructural:
    def test_missing_close_raises(self):
        frame = make_frame().drop(columns=["close"])
        with pytest.raises(MalformedBarsError, match="close"):
            validate_ohlcv_frame(frame)

    def test_non_datetime_index_raises(self):
        frame = make_frame().set_axis(["a", "b", "c"])
        with pytest.raises(MalformedBarsError, match="datetime"):
            validate_ohlcv_frame(frame)

    def test_string_dates_are_converted(self):
        frame = make_frame().set_axis(["2024-01-02", "2024-01-03", "2024-01-04"])
        clean, report = validate_ohlcv_frame(frame)
        assert isinstance(clean.index, pd.DatetimeIndex)
        assert report.clean

    def test_timezone_aware_index_raises(self):
        frame = make_frame()
        frame.index = frame.index.tz_localize("America/New_York")
        with pytest.raises(MalformedBarsError, match="timezone"):
            validate_ohlcv_frame(frame)

    def test_empty_frame_is_legal(self):
        frame = pd.DataFrame({"close": []}, index=pd.DatetimeIndex([]))
        clean, report = validate_ohlcv_frame(frame)
        assert len(clean) == 0
        assert report.n_input == 0
        assert report.clean


class TestCleanPassThrough:
    def test_clean_frame_unchanged(self):
        clean, report = validate_ohlcv_frame(make_frame())
        assert report.clean
        assert report.n_input == report.n_valid == 3
        assert list(clean.columns) == ["open", "high", "low", "close", "volume"]

    def test_close_only_frame_is_valid(self):
        frame = pd.DataFrame(
            {"close": [10.0, 11.0]},
            index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"]),
        )
        clean, report = validate_ohlcv_frame(frame)
        assert report.clean
        assert list(clean.columns) == ["close"]

    def test_unsorted_input_is_sorted(self):
        frame = make_frame().iloc[::-1]
        clean, report = validate_ohlcv_frame(frame)
        assert clean.index.is_monotonic_increasing
        assert report.clean

    def test_unknown_columns_dropped(self):
        frame = make_frame()
        frame["barCount"] = [5, 6, 7]
        clean, _report = validate_ohlcv_frame(frame)
        assert "barCount" not in clean.columns

    def test_missing_optional_value_kept(self):
        frame = make_frame(open=[np.nan, 101.0, 102.0])
        clean, report = validate_ohlcv_frame(frame)
        assert len(clean) == 3
        assert report.clean


class TestRowDrops:
    @pytest.mark.parametrize("bad_close", [0.0, -5.0, np.nan, np.inf, "not-a-number"])
    def test_bad_close_drops_row(self, bad_close):
        frame = make_frame(close=[101.0, bad_close, 103.0])
        clean, report = validate_ohlcv_frame(frame)
        assert len(clean) == 2
        assert len(report.dropped) == 1
        assert report.dropped[0].timestamp == pd.Timestamp("2024-01-03")
        assert "close" in report.dropped[0].reason

    def test_high_below_low_drops_row(self):
        frame = make_frame(high=[101.5, 99.0, 103.5])  # 99.0 < low 100.5
        clean, report = validate_ohlcv_frame(frame)
        assert len(clean) == 2
        assert any("high < low" in issue.reason for issue in report.dropped)

    def test_high_below_close_drops_row(self):
        frame = make_frame(low=[99.5, 100.5, 101.5], high=[101.5, 101.0, 103.5])
        # row 2: high 101.0 < close 102.0 (but >= low), so the OHLC-coherence
        # check must catch it.
        clean, report = validate_ohlcv_frame(frame)
        assert len(clean) == 2
        assert any("high < close" in issue.reason for issue in report.dropped)

    def test_low_above_open_drops_row(self):
        frame = make_frame(low=[99.5, 101.5, 101.5])  # 101.5 > open 101.0
        clean, report = validate_ohlcv_frame(frame)
        assert len(clean) == 2
        assert any("low > open" in issue.reason for issue in report.dropped)

    def test_negative_open_drops_row(self):
        frame = make_frame(open=[100.0, -1.0, 102.0])
        clean, report = validate_ohlcv_frame(frame)
        assert len(clean) == 2
        assert any("open" in issue.reason for issue in report.dropped)

    def test_each_bad_row_reported_once(self):
        # Row violates several checks at once; one report entry, first reason.
        frame = make_frame(close=[101.0, -1.0, 103.0], high=[101.5, 99.0, 103.5])
        clean, report = validate_ohlcv_frame(frame)
        assert len(clean) == 2
        assert len(report.dropped) == 1

    def test_all_rows_bad_yields_empty_frame(self):
        frame = make_frame(close=[-1.0, -1.0, -1.0])
        clean, report = validate_ohlcv_frame(frame)
        assert len(clean) == 0
        assert report.n_valid == 0
        assert len(report.dropped) == 3


class TestDuplicatesAndRepairs:
    def test_duplicate_timestamps_keep_last(self):
        frame = make_frame()
        duplicated = pd.concat([frame, frame.iloc[[1]].assign(close=102.4)])
        clean, report = validate_ohlcv_frame(duplicated)
        assert len(clean) == 3
        assert clean.loc[pd.Timestamp("2024-01-03"), "close"] == 102.4
        assert any("duplicate" in issue.reason for issue in report.dropped)

    def test_negative_volume_repaired_not_dropped(self):
        frame = make_frame(volume=[1000.0, -1.0, 1200.0])
        clean, report = validate_ohlcv_frame(frame)
        assert len(clean) == 3  # row kept
        assert np.isnan(clean["volume"].iloc[1])
        assert len(report.repaired) == 1
        assert not report.dropped


class TestReport:
    def test_summary_mentions_counts_and_reasons(self):
        frame = make_frame(close=[101.0, -1.0, 103.0], volume=[1000.0, 1100.0, -1.0])
        _clean, report = validate_ohlcv_frame(frame)
        text = report.summary()
        assert "3 bars in" in text
        assert "2 valid" in text
        assert "1 dropped" in text
        assert "1 repaired" in text

    def test_clean_summary_is_terse(self):
        _clean, report = validate_ohlcv_frame(make_frame())
        assert report.summary() == "3 bars in, 3 valid"

    def test_report_is_immutable(self):
        _clean, report = validate_ohlcv_frame(make_frame())
        assert isinstance(report, ValidationReport)
        with pytest.raises(AttributeError):
            report.n_input = 99
