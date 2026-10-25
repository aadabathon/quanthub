"""Portfolio service and position store (P2, P3): snapshots kept, holdings
valued and risked, gaps named rather than hidden."""

import io
from datetime import date, datetime

import pytest

from quant_hub.data.positions import Holding, OptionContract, Position, PositionSnapshot
from quant_hub.data.sources import SyntheticSource
from quant_hub.importers.positions_csv import read_positions_csv
from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import PortfolioService, example_snapshot
from quant_hub.storage.position_store import PositionStore, PositionStoreError

NOW = datetime(2026, 10, 16)


@pytest.fixture
def store():
    s = PositionStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def service(store):
    return PortfolioService(MarketService(sources=[SyntheticSource()]), store)


def snapshot(*positions, as_of=NOW):
    return PositionSnapshot(tuple(positions), as_of, "Generic", "test.csv")


def equity(symbol, shares, price, account="Brokerage"):
    return Position(symbol, Holding.EQUITY, shares, account, price, shares * price)


class TestStore:
    def test_snapshots_round_trip_and_are_kept(self, store):
        call = Position("AAPL261218C250", Holding.OPTION, -1, "Brokerage", 4.15, -415.0, None,
                        "AAPL CALL", OptionContract("AAPL", "call", 250.0, date(2026, 12, 18)))
        first = snapshot(equity("AAPL", 50, 227.5), call,
                         Position("CASH", Holding.CASH, 1000.0, "Brokerage", value=1000.0))
        a = store.save(first, imported_at=datetime(2026, 10, 1))
        b = store.save(snapshot(equity("VTI", 10, 289.1)), imported_at=datetime(2026, 10, 2))
        assert [s.id for s in store.snapshots()] == [b, a]             # newest first
        assert store.load(a) == first
        with pytest.raises(PositionStoreError):
            store.load(99)


class TestAnalyse:
    def test_values_weights_and_cash(self, service):
        snap = snapshot(equity("AAA", 100, 50.0), equity("BBB", 200, 25.0),
                        Position("CASH", Holding.CASH, 10_000.0, "Brokerage", value=10_000.0))
        view = service.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW)
        assert view.equity == pytest.approx(20_000.0)
        assert view.cash == 10_000.0
        assert sum(r.weight for r in view.rows) == pytest.approx(0.5)
        assert sum(r.risk_share for r in view.rows) == pytest.approx(1.0)
        assert view.left_out == () and view.unmodelled == 0

    def test_an_option_is_priced_and_counted_by_delta(self, service):
        put = Position("AAA261218P45", Holding.OPTION, 2, "Brokerage", 1.20, 240.0, None, "",
                       OptionContract("AAA", "put", 45.0, date(2026, 12, 18)))
        view = service.analyse(snapshot(equity("AAA", 100, 50.0), put), None, "synthetic",
                               "SPY", 730, 0.04, now=NOW)
        (row,) = view.rows
        assert view.options == 1 and row.option_delta < 0       # long puts are short delta
        assert view.equity == pytest.approx(5_000 + 240)

    def test_expired_options_are_named_and_left_out(self, service):
        old = Position("AAA250117C60", Holding.OPTION, 1, "Brokerage", 0.0, 0.0, None, "",
                       OptionContract("AAA", "call", 60.0, date(2025, 1, 17)))
        view = service.analyse(snapshot(equity("AAA", 100, 50.0), old), None, "synthetic", "SPY",
                               730, 0.04, now=NOW)
        assert view.options == 0 and any("expired" in note for note in view.left_out)

    def test_a_holding_without_history_is_named_with_its_share(self, store):
        # The "file" source has only what was imported: nothing for BBB.
        from quant_hub.services.file_import import FileSource
        from quant_hub.storage.bar_store import BarStore, InstrumentKey
        import numpy as np
        import pandas as pd
        bars = BarStore(":memory:")
        dates = pd.bdate_range("2024-06-03", "2026-10-16")
        closes = 50 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, len(dates))))
        for symbol in ("AAA", "SPY"):
            bars.insert_bars(InstrumentKey(symbol), "file", "1 day",
                             pd.DataFrame({"close": closes}, index=dates))
        service = PortfolioService(MarketService(sources=[FileSource(bars)]), store)
        snap = snapshot(equity("AAA", 100, 50.0), equity("BBB", 100, 50.0))
        view = service.analyse(snap, None, "file", "SPY", 730, 0.04, now=NOW)
        assert [r.symbol for r in view.rows] == ["AAA"]
        assert view.unmodelled == pytest.approx(0.5)
        assert any(note.startswith("BBB: no history") for note in view.left_out)

    def test_offline_prices_are_named_with_their_staleness(self, store):
        # V0: a source serving stored bars says so, and the view carries it.
        from quant_hub.data.types import PriceBasis, PriceHistory

        class StoredWhileOffline:
            name = "stored"
            last = datetime(2026, 10, 9)                     # a Friday

            def get_history(self, symbol, start, end, basis=PriceBasis.TRADED):
                h = SyntheticSource().get_history(symbol, start, self.last, basis)
                return PriceHistory(h.symbol, self.name, h.frame, h.basis,
                                    offline_reason="not connected")

            def get_quote(self, symbol):
                raise AssertionError("not used")

        service = PortfolioService(MarketService(sources=[StoredWhileOffline()]), store)
        snap = snapshot(equity("AAA", 100, 50.0), equity("BBB", 10, 25.0))
        view = service.analyse(snap, None, "stored", "SPY", 730, 0.04, now=NOW)
        assert view.offline_reason == "not connected"
        assert view.report.pnl.index[-1] == datetime(2026, 10, 9)
        assert view.sessions_missed == 4                     # Mon Oct 12 - Thu Oct 15
        online = PortfolioService(MarketService(sources=[SyntheticSource()]), store)
        fresh = online.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW)
        assert fresh.offline_reason is None and fresh.sessions_missed == 0

    def test_one_account_at_a_time(self, service):
        snap = snapshot(equity("AAA", 100, 50.0, "Individual"), equity("BBB", 10, 25.0, "ROTH"))
        view = service.analyse(snap, "ROTH", "synthetic", "SPY", 730, 0.04, now=NOW)
        assert [r.symbol for r in view.rows] == ["BBB"] and view.equity == 250.0
        with pytest.raises(ValueError, match="no positions in account"):
            service.analyse(snap, "Joint", "synthetic", "SPY", 730, 0.04, now=NOW)

    def test_import_then_analyse_a_fidelity_file(self, service, tmp_path):
        from test_positions_csv import FIDELITY
        path = tmp_path / "Portfolio_Positions.csv"
        path.write_text(FIDELITY, encoding="utf-8")
        snapshot_id, snap = service.import_positions(path)
        assert service.load(snapshot_id) == snap
        view = service.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW)
        assert {r.symbol for r in view.rows} == {"AAPL", "VTI", "QQQ"}
        assert any("expired" in note for note in view.left_out)      # its Jan 2026 call

    def test_one_export_per_account_is_one_snapshot(self, service, tmp_path):
        from test_positions_csv import view_export
        paths = []
        for account in ("Individual", "ROTH IRA"):
            path = tmp_path / f"{account}.csv"
            path.write_text(view_export(account), encoding="utf-8")
            paths.append(path)
        snapshot_id, snap = service.import_positions(paths)
        assert [s.id for s in service.snapshots()] == [snapshot_id]   # one import, kept once
        assert snap.accounts == ("Individual", "ROTH IRA")
        both = service.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW)
        roth = service.analyse(snap, "ROTH IRA", "synthetic", "SPY", 730, 0.04, now=NOW)
        assert both.equity == pytest.approx(2 * roth.equity)           # identical made-up files


class TestHistoryProblems:
    """Every failure says what happened: before this, a portfolio whose
    histories all failed (IBKR not connected) reported "0 common days of
    returns; the model needs 60"."""

    @staticmethod
    def file_service(store, histories: dict[str, int]):
        """The ``file`` source holding ``days`` daily closes per symbol, up to NOW."""
        import numpy as np
        import pandas as pd
        from quant_hub.services.file_import import FileSource
        from quant_hub.storage.bar_store import BarStore, InstrumentKey
        bars = BarStore(":memory:")
        for i, (symbol, days) in enumerate(histories.items()):
            dates = pd.bdate_range(end="2026-10-16", periods=days)
            moves = np.random.default_rng(i).normal(0, 0.01, days)
            bars.insert_bars(InstrumentKey(symbol), "file", "1 day",
                             pd.DataFrame({"close": 50 * np.exp(np.cumsum(moves))}, index=dates))
        return PortfolioService(MarketService(sources=[FileSource(bars)]), store)

    def test_a_short_history_is_left_out_not_shrinking_the_rest(self, store):
        service = self.file_service(store, {"AAA": 500, "BBB": 30, "SPY": 500})
        snap = snapshot(equity("AAA", 100, 50.0), equity("BBB", 100, 50.0))
        view = service.analyse(snap, None, "file", "SPY", 730, 0.04, now=NOW)
        assert [r.symbol for r in view.rows] == ["AAA"]
        assert len(view.report.pnl) > 400                       # AAA's own window, not BBB's
        assert any(note.startswith("BBB: only 29 days") for note in view.left_out)
        assert view.unmodelled == pytest.approx(0.5)

    def test_no_history_at_all_says_why(self, store):
        service = self.file_service(store, {"SPY": 500})
        snap = snapshot(equity("AAA", 100, 50.0), equity("BBB", 100, 50.0))
        with pytest.raises(ValueError, match=r"No holding's price history loaded from file"
                                             r".*AAA: no history from file"):
            service.analyse(snap, None, "file", "SPY", 730, 0.04, now=NOW)

    def test_a_disconnected_broker_points_at_connect(self, store):
        from quant_hub.broker.types import NotConnectedError

        class Disconnected:
            name = "ibkr"

            def get_history(self, symbol, start, end, basis=None):
                raise NotConnectedError("not connected to IBKR")

            def get_quote(self, symbol):
                raise NotConnectedError("not connected to IBKR")

        service = PortfolioService(MarketService(sources=[Disconnected()]), store)
        with pytest.raises(ValueError, match="Press Connect IBKR"):
            service.analyse(snapshot(equity("AAA", 100, 50.0)), None, "ibkr", "SPY", 730,
                            0.04, now=NOW)

    def test_only_cash_has_no_market_risk(self, service):
        cash = Position("CASH", Holding.CASH, 5_000.0, "Brokerage", value=5_000.0)
        with pytest.raises(ValueError, match="only cash"):
            service.analyse(snapshot(cash), None, "synthetic", "SPY", 730, 0.04, now=NOW)


def test_the_example_is_a_hundred_thousand_dollars():
    market = MarketService(sources=[SyntheticSource()])
    snap = example_snapshot(market, "synthetic")
    assert sum(p.market_value for p in snap.positions) == pytest.approx(100_000)
    assert snap.source == "Example" and snap.positions[-1].kind is Holding.CASH
