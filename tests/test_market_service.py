"""Tests for the market service facade (source registry and routing).

Since M6 (D23) there is NO active/global source: every call names its
source explicitly, so one caller can never change what another caller's
requests mean.
"""

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
    return MarketService(sources=[a, b]), a, b


class TestConstruction:
    def test_requires_at_least_one_source(self):
        with pytest.raises(ValueError):
            MarketService(sources=[])

    def test_rejects_duplicate_names(self):
        with pytest.raises(ValueError, match="duplicate"):
            MarketService(sources=[RecordingSource("a"), RecordingSource("a")])

    def test_has_no_global_source_state(self):
        service, _, _ = make_service()
        assert not hasattr(service, "active_source")


class TestRouting:
    def test_history_routes_to_named_source(self):
        service, a, b = make_service()
        service.get_history(
            "SPY", datetime(2022, 1, 1), datetime(2023, 1, 1), source="alpha"
        )
        assert a.calls == [("history", "SPY")]
        assert b.calls == []

    def test_quote_routes_to_named_source(self):
        service, a, b = make_service()
        service.get_quote("SPY", source="beta")
        assert b.calls == [("quote", "SPY")]
        assert a.calls == []

    def test_callers_are_independent(self):
        # Two callers using different sources never affect each other.
        service, a, b = make_service()
        service.get_quote("SPY", source="alpha")
        service.get_quote("SPY", source="beta")
        service.get_quote("QQQ", source="alpha")
        assert a.calls == [("quote", "SPY"), ("quote", "QQQ")]
        assert b.calls == [("quote", "SPY")]

    def test_source_is_required(self):
        service, _, _ = make_service()
        with pytest.raises(TypeError):
            service.get_quote("SPY")  # no implicit default source

    def test_unknown_source_raises(self):
        service, _, _ = make_service()
        with pytest.raises(UnknownSourceError):
            service.get_quote("SPY", source="gamma")

    def test_source_names_listed(self):
        service, _, _ = make_service()
        assert service.source_names == ["alpha", "beta"]
