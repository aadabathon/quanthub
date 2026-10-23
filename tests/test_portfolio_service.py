"""Portfolio service and position store (P2, P3): snapshots kept, holdings
valued and risked, gaps named rather than hidden."""

import io
from datetime import date, datetime

import numpy as np
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


class TestRegressions:
    def test_holdings_and_the_portfolio_on_the_benchmark(self, service):
        from quant_hub.services.portfolio import PORTFOLIO_ROW
        snap = snapshot(equity("AAA", 100, 50.0), equity("BBB", 200, 25.0),
                        Position("CASH", Holding.CASH, 5_000.0, "Brokerage", value=5_000.0))
        view = service.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW)
        reg = view.regressions
        assert reg is not None and view.regression_note == ""
        assert list(reg.fits) == [*view.report.symbols, PORTFOLIO_ROW]
        # Stocks only: the portfolio's coefficients are the weighted holdings'.
        w = dict(zip(view.report.symbols, view.report.weights))
        for i in range(2):
            assert reg.fits[PORTFOLIO_ROW].coef[i] == pytest.approx(
                sum(w[s] * reg.fits[s].coef[i] for s in w), abs=1e-12)
        beta, error = reg.beta(PORTFOLIO_ROW)
        assert beta == pytest.approx(view.report.beta, abs=1e-9) and error > 0
        assert set(reg.rolling) == set(reg.fits) and list(reg.rolling["AAA"].columns) == ["beta", "se"]
        assert list(reg.correlation.columns) == list(view.report.symbols)
        assert reg.lag_beta("AAA") is None

    def test_the_benchmark_held_has_no_residual_links(self, service):
        snap = snapshot(equity("SPY", 10, 500.0), equity("AAA", 100, 50.0),
                        equity("BBB", 200, 25.0))
        reg = service.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW).regressions
        assert reg.fits["SPY"].r2 == pytest.approx(1.0)
        assert reg.correlation.loc["SPY"].isna().all()
        assert np.isfinite(reg.correlation.loc["AAA", "BBB"])

    def test_a_lag_makes_beta_the_dimson_sum(self, service):
        snap = snapshot(equity("AAA", 100, 50.0))
        reg = service.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW,
                              dimson_lags=1).regressions
        fit = reg.fits["AAA"]
        lag = reg.lag_beta("AAA")
        assert reg.beta("AAA")[0] == pytest.approx(fit.value("SPY") + lag[0])

    def test_extra_factors_are_partial_betas_and_leave_the_risk_alone(self, service):
        snap = snapshot(equity("AAA", 100, 50.0), equity("BBB", 200, 25.0))
        plain = service.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW)
        both = service.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW,
                               factors=("QQQ", "SPY", "TLT", "QQQ"))   # the benchmark and repeats drop
        reg = both.regressions
        assert reg.factors == ("QQQ", "TLT")
        assert reg.fits["AAA"].names == ("alpha", "SPY", "QQQ", "TLT")
        value, error = reg.factor_beta("AAA", "QQQ")
        assert np.isfinite(value) and error > 0
        assert both.report.vol == plain.report.vol                       # the risk model is untouched
        assert both.report.beta == plain.report.beta

    def test_a_factor_without_history_is_named(self, store):
        from quant_hub.services.file_import import FileSource
        from quant_hub.storage.bar_store import BarStore, InstrumentKey
        import pandas as pd
        bars = BarStore(":memory:")
        dates = pd.bdate_range("2024-06-03", "2026-10-16")
        rng = np.random.default_rng(2)
        for symbol in ("AAA", "SPY"):
            closes = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, len(dates))))
            bars.insert_bars(InstrumentKey(symbol), "file", "1 day",
                             pd.DataFrame({"close": closes}, index=dates))
        service = PortfolioService(MarketService(sources=[FileSource(bars)]), store)
        view = service.analyse(snapshot(equity("AAA", 100, 50.0)), None, "file", "SPY", 730,
                               0.04, now=NOW, factors=("ZZZ",))
        assert view.regressions is not None and view.regressions.factors == ()
        assert "ZZZ" in view.regression_note

    def test_no_benchmark_history_means_no_regressions(self, store):
        from quant_hub.services.file_import import FileSource
        from quant_hub.storage.bar_store import BarStore, InstrumentKey
        import numpy as np
        import pandas as pd
        bars = BarStore(":memory:")
        dates = pd.bdate_range("2024-06-03", "2026-10-16")
        closes = 50 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, len(dates))))
        bars.insert_bars(InstrumentKey("AAA"), "file", "1 day",
                         pd.DataFrame({"close": closes}, index=dates))
        service = PortfolioService(MarketService(sources=[FileSource(bars)]), store)
        view = service.analyse(snapshot(equity("AAA", 100, 50.0)), None, "file", "SPY", 730,
                               0.04, now=NOW)
        assert view.regressions is None and "No SPY history" in view.regression_note


class TestGrowth:
    def test_sleeves_are_underlyings_and_the_attribution_adds_up(self, service):
        put = Position("AAA261218P45", Holding.OPTION, 2, "Brokerage", 1.20, 240.0, None, "",
                       OptionContract("AAA", "put", 45.0, date(2026, 12, 18)))
        snap = snapshot(equity("AAA", 100, 50.0), equity("BBB", 200, 25.0), put)
        view = service.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW)
        growth = view.growth
        assert growth is not None and view.growth_note == ""
        assert {s.name for s in growth.sleeves} == {"AAA", "BBB"}
        assert len(growth.sleeve("AAA").positions) == 2                # its shares and its put
        assert sum(s.contribution for s in growth.sleeves) == pytest.approx(
            growth.growth - 0.04, abs=1e-12)
        assert view.book is not None and view.book.contracts == 1
        assert view.book.delta < 0                                      # long puts: short delta

    def test_stocks_only_growth_equals_the_sizing_card(self, service):
        snap = snapshot(equity("AAA", 100, 50.0), equity("BBB", 200, 25.0),
                        Position("CASH", Holding.CASH, 5_000.0, "Brokerage", value=5_000.0))
        view = service.analyse(snap, None, "synthetic", "SPY", 730, 0.04, now=NOW)
        r = view.report
        assert view.growth.growth == pytest.approx(r.growth(r.net_exposure), abs=1e-12)
        assert view.book is None


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
