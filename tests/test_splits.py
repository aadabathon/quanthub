"""Split-sized overnight gaps (D57, ``data/splits.py``): the docstring's
claims, checked. Every listed ratio is found, at its session; ordinary and
volatile paths raise nothing; the tolerance holds at its edges; a bad open
print is skipped; a close-only series is tested on its close."""

import math
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.splits import (
    DAY_MOVE_LIMIT,
    GAP_TOLERANCE,
    SplitGap,
    split_gaps,
)
from quant_hub.data.types import PriceHistory


def bars(n=500, overnight=0.015, intraday=0.02, seed=7):
    """A daily OHLC path: each open gaps from the last close, each close moves
    from its open. Log moves are Gaussian, so the tests know their tails."""
    rng = np.random.default_rng(seed)
    gap, day = rng.normal(0, overnight, n), rng.normal(0, intraday, n)
    gap[0] = 0.0
    opens, closes = np.empty(n), np.empty(n)
    last = 100.0
    for i in range(n):
        opens[i] = last * math.exp(gap[i])
        closes[i] = opens[i] * math.exp(day[i])
        last = closes[i]
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes),
                         "low": np.minimum(opens, closes), "close": closes,
                         "volume": np.full(n, 1e6)}, index=pd.bdate_range("2001-01-02", periods=n))


def unadjusted(frame, at, k):
    """``frame`` with a k-for-1 split left in from session ``at`` on."""
    out = frame.copy()
    out.iloc[at:, :4] /= k
    return out


def two_days(gap, day_move=0.0, columns=("open", "close")):
    """Two sessions: a close of 100, then an open at ``gap`` times it and a
    close ``day_move`` (log) from that open."""
    o = 100 * gap
    frame = pd.DataFrame({"open": [100.0, o], "close": [100.0, o * math.exp(day_move)]},
                         index=pd.bdate_range("2003-02-14", periods=2))
    return frame[list(columns)]


class TestFound:
    @pytest.mark.parametrize("k, label", [(1.5, "3:2"), (2, "2:1"), (3, "3:1"), (4, "4:1"),
                                          (7, "7:1"), (10, "10:1"), (20, "20:1"),
                                          (1 / 2, "1:2"), (1 / 3, "1:3"), (1 / 4, "1:4"),
                                          (1 / 10, "1:10")])
    def test_each_ratio_is_named_at_its_session(self, k, label):
        frame = unadjusted(bars(), 300, k)
        (found,) = split_gaps(frame)
        assert found.day == frame.index[300].to_pydatetime()
        assert found.ratio == label
        assert abs(math.log(found.gap * k)) <= GAP_TOLERANCE

    def test_several_splits_come_oldest_first(self):
        frame = unadjusted(unadjusted(bars(), 100, 2), 400, 3)
        assert [(g.day, g.ratio) for g in split_gaps(frame)] == [
            (frame.index[100].to_pydatetime(), "2:1"), (frame.index[400].to_pydatetime(), "3:1")]

    def test_a_split_on_a_news_morning_is_still_found(self):
        # The widest real one: a 2:1 on the market's reopening after 9/11.
        assert [g.ratio for g in split_gaps(two_days(0.469))] == ["2:1"]

    def test_unsorted_input_is_read_in_date_order(self):
        frame = unadjusted(bars(), 300, 2)
        assert split_gaps(frame.iloc[::-1]) == split_gaps(frame)


class TestTolerance:
    @pytest.mark.parametrize("err", [-0.069, 0.069])
    def test_inside_the_band(self, err):
        assert len(split_gaps(two_days(0.5 * math.exp(err)))) == 1

    @pytest.mark.parametrize("err", [-0.071, 0.071])
    def test_outside_the_band(self, err):
        assert split_gaps(two_days(0.5 * math.exp(err))) == ()

    @pytest.mark.parametrize("gap", [0.80, 0.55, 0.45, 1.30, 1.70, 0.24 * math.exp(-0.08)])
    def test_large_moves_between_bands_are_not_named(self, gap):
        assert split_gaps(two_days(gap)) == ()

    def test_fivefold_and_beyond_needs_no_listed_ratio(self):
        assert split_gaps(two_days(1 / 5 * math.exp(GAP_TOLERANCE - 0.001)))[0].ratio == "5:1"
        assert split_gaps(two_days(37.0))[0].ratio == "1:37"


class TestNoFalseAlarms:
    def test_a_volatile_path_names_nothing(self):
        # 4% overnight and 5% intraday (about 100% a year): the nearest band
        # edge, ln(3/2) − τ = 0.335, is over 8 overnight σ away.
        assert split_gaps(bars(n=20_000, overnight=0.04, intraday=0.05, seed=11)) == ()

    def test_an_adjusted_split_leaves_no_gap(self):
        frame = unadjusted(bars(), 300, 2)
        frame.iloc[:300, :4] /= 2                    # what an adjusted series does
        assert split_gaps(frame) == ()


class TestOpens:
    def test_a_bad_open_print_is_skipped(self):
        # A real bar: opened at $0.255 after a $10.575 close, closed at $10.51.
        assert split_gaps(two_days(0.255 / 10.575, math.log(10.51 / 0.255))) == ()

    def test_a_day_past_the_move_limit_is_skipped(self):
        assert split_gaps(two_days(0.5, DAY_MOVE_LIMIT + 0.01)) == ()
        assert len(split_gaps(two_days(0.5, DAY_MOVE_LIMIT - 0.01))) == 1

    def test_a_close_only_series_is_tested_on_its_close(self):
        assert [g.ratio for g in split_gaps(two_days(0.5, columns=("close",)))] == ["2:1"]

    def test_a_missing_open_falls_back_to_the_close(self):
        frame = unadjusted(bars(), 300, 2)
        frame.iloc[300, 0] = np.nan
        (found,) = split_gaps(frame)
        assert found.gap == pytest.approx(frame["close"].iloc[300] / frame["close"].iloc[299])


class TestInputs:
    def test_needs_a_close(self):
        with pytest.raises(ValueError, match="close"):
            split_gaps(two_days(0.5, columns=("open",)))

    def test_needs_dates(self):
        with pytest.raises(ValueError, match="DatetimeIndex"):
            split_gaps(pd.DataFrame({"close": [1.0, 0.5]}))

    def test_one_bar_has_no_gap(self):
        assert split_gaps(two_days(0.5).iloc[:1]) == ()


def test_every_history_names_its_gaps():
    frame = unadjusted(bars(), 300, 2)
    history = PriceHistory("XYZ", "fixture", frame)
    assert history.split_gaps == split_gaps(frame)
    assert PriceHistory("XYZ", "fixture", bars()).split_gaps == ()


def test_pages_say_it_in_words():
    from quant_hub.gui.pages.base import history_note, split_warning

    one = (SplitGap(datetime(2003, 2, 18), 0.51, "2:1"),)
    assert split_warning("XYZ", one) == (
        "XYZ has an overnight gap the size of a stock split: ×0.51 on Feb 18, 2003 (2:1). "
        "Some older IBKR history isn't split-adjusted, so check before trusting numbers "
        "that include it.")
    many = tuple(SplitGap(datetime(1999 + i, 3, 1), 0.5, "2:1") for i in range(5))
    assert "5 overnight gaps" in split_warning("XYZ", many)
    assert split_warning("XYZ", many).count("×0.50") == 3 and "and 2 more" in split_warning("XYZ", many)
    history = PriceHistory("XYZ", "fixture", unadjusted(bars(), 300, 2))
    assert history_note(history) == f" · split-sized gap {history.frame.index[300]:%b %d, %Y}: check it"
    assert history_note(PriceHistory("XYZ", "fixture", bars())) == ""
