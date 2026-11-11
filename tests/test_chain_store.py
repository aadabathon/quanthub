"""Chain snapshots (C2, D33): the SQLite store round-trips chains exactly,
and the chain service saves every live fetch and serves "stored"."""

from datetime import datetime

import pytest

from quant_hub.data.options import ChainRequest, OptionChain, OptionQuote
from quant_hub.services.option_chains import (
    STORED,
    ChainService,
    NoStoredChainError,
    UnknownChainSourceError,
)
from quant_hub.storage.chain_store import ChainStore, ChainStoreError


def chain(symbol="SPY", as_of=datetime(2026, 10, 15, 12, 30, 5), spot=771.5) -> OptionChain:
    return OptionChain(symbol, "ibkr", as_of, spot, 0.04, 0.0, (
        OptionQuote("call", 775.0, 29.27, 9.1, 9.2, reference_iv=0.128),
        OptionQuote("put", 770.0, 29.27, 8.0, 8.1),
        OptionQuote("put", 700.0, 6.96, 0.0, 0.05),
    ))


class TestChainStore:
    def test_round_trip_is_exact(self):
        store = ChainStore(":memory:")
        original = chain()
        loaded = store.load(store.save(original))
        assert loaded == original

    def test_saving_the_same_snapshot_twice_is_a_no_op(self):
        store = ChainStore(":memory:")
        assert store.save(chain()) == store.save(chain())
        assert len(store.snapshots()) == 1

    def test_latest_and_listing_are_newest_first_per_symbol(self):
        store = ChainStore(":memory:")
        store.save(chain(as_of=datetime(2026, 10, 14, 15, 0)))
        newest = store.save(chain(as_of=datetime(2026, 10, 15, 15, 0), spot=780.0))
        store.save(chain(symbol="SPX", as_of=datetime(2026, 10, 16, 9, 0), spot=7744.0))
        spy = store.snapshots("spy")
        assert [s.id for s in spy][0] == newest and len(spy) == 2 and spy[0].quotes == 3
        assert store.latest("SPY").spot == 780.0
        assert store.latest("QQQ") is None
        assert [s.symbol for s in store.snapshots()] == ["SPX", "SPY", "SPY"]

    def test_snapshots_survive_reopening(self, tmp_path):
        path = tmp_path / "market_data.db"
        first = ChainStore(path)
        first.save(chain())
        first.close()
        assert ChainStore(path).latest("SPY") == chain()

    def test_missing_snapshot_raises(self):
        with pytest.raises(ChainStoreError):
            ChainStore(":memory:").load(42)


class _FakeSource:
    name = "ibkr"

    def __init__(self):
        self.requests = []

    def get_chain(self, symbol, request):
        self.requests.append((symbol, request))
        return chain(symbol=symbol)


class TestChainService:
    def test_live_fetches_are_saved_and_stored_serves_the_latest(self):
        store, source = ChainStore(":memory:"), _FakeSource()
        service = ChainService([source], store)
        assert service.source_names == ["ibkr", STORED]
        live, snapshot = service.get_chain(" spy ", "ibkr", ChainRequest(strikes_per_expiry=9))
        assert source.requests[0][0] == "SPY" and source.requests[0][1].strikes_per_expiry == 9
        assert store.snapshots("SPY")[0].id == snapshot
        stored, same = service.get_chain("SPY", STORED)
        assert (stored, same) == (live, snapshot)
        assert len(source.requests) == 1        # "stored" never touches the provider

    def test_errors_name_what_is_missing(self):
        service = ChainService([_FakeSource()], ChainStore(":memory:"))
        with pytest.raises(NoStoredChainError, match="no saved SPY chain"):
            service.get_chain("SPY", STORED)
        with pytest.raises(UnknownChainSourceError):
            service.get_chain("SPY", "nowhere")

    def test_stored_is_a_reserved_name(self):
        source = _FakeSource()
        source.name = STORED
        with pytest.raises(ValueError):
            ChainService([source], ChainStore(":memory:"))
