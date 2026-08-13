"""Tests for the synthetic data source's contract behavior."""

from datetime import datetime

import pytest

from quant_hub.data.sources import DataSource, SyntheticSource


class TestSyntheticSource:
    def setup_method(self):
        self.source = SyntheticSource()
        self.start = datetime(2022, 1, 1)
        self.end = datetime(2023, 1, 1)

    def test_conforms_to_protocol(self):
        assert isinstance(self.source, DataSource)

    def test_history_covers_requested_window(self):
        h = self.source.get_history("SPY", self.start, self.end)
        assert h.symbol == "SPY"
        assert h.source == "synthetic"
        assert h.start >= self.start
        assert h.end <= self.end
        assert len(h) > 200  # roughly a year of business days

    def test_deterministic_across_instances(self):
        other = SyntheticSource()
        a = self.source.get_history("SPY", self.start, self.end)
        b = other.get_history("SPY", self.start, self.end)
        assert a.closes.equals(b.closes)

    def test_different_symbols_get_different_worlds(self):
        a = self.source.get_history("SPY", self.start, self.end)
        b = self.source.get_history("AAPL", self.start, self.end)
        assert not a.closes.equals(b.closes)

    def test_windows_are_consistent_slices_of_one_path(self):
        """Requesting a sub-window must return the same prices as slicing the
        larger window -- the vendor-like property that makes the source usable
        for research."""
        full = self.source.get_history("SPY", datetime(2020, 1, 1), self.end)
        sub = self.source.get_history("SPY", self.start, self.end)
        overlap = full.frame.loc[sub.frame.index]
        assert overlap["close"].equals(sub.closes)

    def test_symbol_is_normalized(self):
        a = self.source.get_history(" spy ", self.start, self.end)
        b = self.source.get_history("SPY", self.start, self.end)
        assert a.symbol == "SPY"
        assert a.closes.equals(b.closes)

    def test_rejects_inverted_range(self):
        with pytest.raises(ValueError):
            self.source.get_history("SPY", self.end, self.start)

    def test_rejects_start_before_base_date(self):
        with pytest.raises(ValueError, match="begins"):
            self.source.get_history("SPY", datetime(2000, 1, 1), self.end)

    def test_quote_matches_history_tail(self):
        quote = self.source.get_quote("SPY")
        h = self.source.get_history("SPY", datetime(2015, 1, 2), datetime.now())
        assert quote.price == pytest.approx(float(h.closes.iloc[-1]))
        assert quote.source == "synthetic"
