"""Tests for the canonical data types' validation and derived series."""

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.types import PriceHistory, Quote


def frame_of(closes, index=None):
    if index is None:
        index = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({"close": closes}, index=index)


class TestPriceHistoryValidation:
    def test_valid_history(self):
        h = PriceHistory(symbol="SPY", source="test", frame=frame_of([100, 101, 99]))
        assert len(h) == 3
        assert h.start < h.end

    def test_requires_datetime_index(self):
        frame = pd.DataFrame({"close": [1.0, 2.0]})
        with pytest.raises(ValueError, match="DatetimeIndex"):
            PriceHistory(symbol="X", source="test", frame=frame)

    def test_requires_close_column(self):
        frame = pd.DataFrame(
            {"open": [1.0, 2.0]}, index=pd.bdate_range("2024-01-01", periods=2)
        )
        with pytest.raises(ValueError, match="close"):
            PriceHistory(symbol="X", source="test", frame=frame)

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="empty"):
            PriceHistory(symbol="X", source="test", frame=frame_of([]))

    def test_rejects_nan_and_nonpositive_closes(self):
        with pytest.raises(ValueError, match="non-finite"):
            PriceHistory(symbol="X", source="test", frame=frame_of([100, np.nan]))
        with pytest.raises(ValueError, match="non-positive"):
            PriceHistory(symbol="X", source="test", frame=frame_of([100, 0.0]))

    def test_rejects_duplicate_timestamps(self):
        index = pd.DatetimeIndex(["2024-01-02", "2024-01-02"])
        with pytest.raises(ValueError, match="duplicate"):
            PriceHistory(symbol="X", source="test", frame=frame_of([1, 2], index))

    def test_sorts_unsorted_input(self):
        index = pd.DatetimeIndex(["2024-01-03", "2024-01-02"])
        h = PriceHistory(symbol="X", source="test", frame=frame_of([2.0, 1.0], index))
        assert list(h.closes) == [1.0, 2.0]

    def test_does_not_alias_caller_frame(self):
        frame = frame_of([100.0, 101.0])
        h = PriceHistory(symbol="X", source="test", frame=frame)
        frame.iloc[0, 0] = 999.0
        assert h.closes.iloc[0] == 100.0


class TestDerivedSeries:
    def test_simple_and_log_returns(self):
        h = PriceHistory(symbol="X", source="test", frame=frame_of([100.0, 110.0]))
        assert h.simple_returns().iloc[0] == pytest.approx(0.10)
        assert h.log_returns().iloc[0] == pytest.approx(np.log(1.10))


class TestQuote:
    def test_rejects_bad_price(self):
        from datetime import datetime

        with pytest.raises(ValueError):
            Quote(symbol="X", price=-1.0, timestamp=datetime.now(), source="test")
        with pytest.raises(ValueError):
            Quote(symbol="", price=10.0, timestamp=datetime.now(), source="test")
