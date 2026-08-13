"""Tests for the market service facade (source registry and routing)."""

from datetime import datetime

import pytest

from quant_hub.data.sources import SyntheticSource
from quant_hub.services.market_service import MarketService, UnknownSourceError


class RecordingSource:
    """Minimal DataSource double that records calls."""

    def __init__(self, name):
        self.name = name
        self.calls = []

    def get_history(self, symbol, start, end):
        self.calls.append(("history", symbol))
        return SyntheticSource().get_history(symbol, start, end)

    def get_quote(self, symbol):
        self.calls.append(("quote", symbol))
        return SyntheticSource().get_quote(symbol)


def make_service():
    a, b = RecordingSource("alpha"), RecordingSource("beta")
    return MarketService(sources=[a, b], active="alpha"), a, b


class TestConstruction:
    def test_requires_at_least_one_source(self):
        with pytest.raises(ValueError):
            MarketService(sources=[], active="x")

    def test_rejects_duplicate_names(self):
        with pytest.raises(ValueError, match="duplicate"):
            MarketService(
                sources=[RecordingSource("a"), RecordingSource("a")], active="a"
            )

    def test_rejects_unknown_active_source(self):
        with pytest.raises(UnknownSourceError):
            MarketService(sources=[RecordingSource("a")], active="nope")


class TestRouting:
    def test_routes_to_active_source(self):
        service, a, b = make_service()
        service.get_history("SPY", datetime(2022, 1, 1), datetime(2023, 1, 1))
        assert a.calls == [("history", "SPY")]
        assert b.calls == []

    def test_switching_active_source_reroutes(self):
        service, a, b = make_service()
        service.active_source = "beta"
        service.get_quote("SPY")
        assert b.calls == [("quote", "SPY")]
        assert a.calls == []

    def test_explicit_source_overrides_active(self):
        service, a, b = make_service()
        service.get_quote("SPY", source="beta")
        assert b.calls == [("quote", "SPY")]
        assert service.active_source == "alpha"  # override is per-call

    def test_unknown_source_raises(self):
        service, _, _ = make_service()
        with pytest.raises(UnknownSourceError):
            service.get_quote("SPY", source="gamma")
        with pytest.raises(UnknownSourceError):
            service.active_source = "gamma"

    def test_source_names_listed(self):
        service, _, _ = make_service()
        assert service.source_names == ["alpha", "beta"]
