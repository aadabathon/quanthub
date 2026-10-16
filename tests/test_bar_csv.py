"""Reading daily bars from CSV (F2): the generic layout and vendor presets."""

import io

import numpy as np
import pandas as pd
import pytest

from quant_hub.importers.bar_csv import (
    GENERIC,
    NASDAQ,
    YAHOO,
    BarFileError,
    detect_layout,
    read_bar_csv,
)


def read(text: str):
    return read_bar_csv(io.StringIO(text))


class TestGenericLayout:
    def test_reads_ohlcv_onto_session_dates(self):
        parsed = read("date,open,high,low,close,volume\n"
                      "2024-01-02,472.16,473.67,470.49,472.65,123623700\n"
                      "2024-01-03,470.43,471.19,468.17,468.79,103585900\n")
        assert parsed.layout is GENERIC and list(parsed.bars) == [None]
        frame = parsed.bars[None]
        assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
        assert list(frame.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
        assert frame.index.tz is None
        assert frame.loc["2024-01-03", "close"] == 468.79

    def test_any_case_order_and_extra_columns(self):
        parsed = read(" Close ,Notes,DATE\n101.5,hi,2024-02-01\n")
        frame = parsed.bars[None]
        assert list(frame.columns) == ["close"]               # close-only is fine
        assert frame.iloc[0, 0] == 101.5

    def test_a_symbol_column_splits_the_file(self):
        parsed = read("symbol,date,close\nspy,2024-01-02,472.6\nQQQ,2024-01-02,409.5\n"
                      "SPY,2024-01-03,468.8\n")
        assert list(parsed.bars) == ["SPY", "QQQ"]
        assert len(parsed.bars["SPY"]) == 2 and len(parsed.bars["QQQ"]) == 1

    def test_dollar_signs_separators_and_missing_values(self):
        frame = read('date,close,volume\n2024-01-02,"$1,234.50",null\n'
                     "2024-01-03,,1000\n").bars[None]
        assert frame["close"].iloc[0] == 1234.5
        assert np.isnan(frame["volume"].iloc[0]) and np.isnan(frame["close"].iloc[1])

    def test_stooq_files_are_generic(self):
        assert detect_layout(["Date", "Open", "High", "Low", "Close", "Volume"]) is GENERIC


class TestPresets:
    def test_yahoo_is_recognized_by_adj_close_and_takes_close(self):
        parsed = read("Date,Open,High,Low,Close,Adj Close,Volume\n"
                      "2024-01-02,472.16,473.67,470.49,472.65,466.71,123623700\n"
                      "2024-01-03,null,null,null,null,null,null\n")
        assert parsed.layout is YAHOO
        frame = parsed.bars[None]
        assert frame["close"].iloc[0] == 472.65 and "adj close" not in frame.columns
        assert np.isnan(frame["close"].iloc[1])               # validation will drop it

    def test_nasdaq_dates_dollars_and_order(self):
        parsed = read("Date,Close/Last,Volume,Open,High,Low\n"
                      "01/03/2024,$468.79,103585900,$470.43,$471.19,$468.17\n"
                      "01/02/2024,$472.65,123623700,$472.16,$473.67,$470.49\n")
        assert parsed.layout is NASDAQ
        frame = parsed.bars[None]
        assert list(frame.index) == [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-02")]
        assert frame.loc["2024-01-02", "close"] == 472.65   # newest-first; validation sorts


class TestProblems:
    def test_unrecognized_columns_say_what_was_found(self):
        with pytest.raises(BarFileError, match=r"unrecognized columns \['day', 'price'\]"):
            read("day,price\n2024-01-02,1\n")

    def test_an_unreadable_date_names_its_row(self):
        with pytest.raises(BarFileError, match=r"row 3: date '2024-13-01' isn't YYYY-MM-DD"):
            read("date,close\n2024-01-02,1\n2024-13-01,2\n")

    def test_a_preset_date_format_is_enforced(self):
        with pytest.raises(BarFileError, match="isn't MM/DD/YYYY"):
            read("Date,Close/Last\n2024-01-02,$1\n")

    @pytest.mark.parametrize("text", ["", "date,close\n"])
    def test_empty_files(self, text):
        with pytest.raises(BarFileError):
            read(text)

    def test_an_empty_symbol_names_its_row(self):
        with pytest.raises(BarFileError, match="row 3: the symbol is empty"):
            read("symbol,date,close\nSPY,2024-01-02,1\n ,2024-01-03,2\n")
