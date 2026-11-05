"""The hedge lab's service (HEDGE U3, services/hedge.py), on made-up stores:
the β proxy from French's names, VIX aligned and missing data named, never
fetched; option candidates on VIX's window; today's mix equal to Portfolio's
replay (plus its cash's interest) and h = 0 changing nothing; today's shape
against a known SSVI surface; streams from stored bars; today's cost and
its percentile."""

import math
from dataclasses import replace
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.series import FetchedSeries, SeriesMeta
from quant_hub.models.svi import SSVISurface
from quant_hub.models.vol_surface import SyntheticChainSpec, synthetic_chain
from quant_hub.services.external_data import ExternalDataService
from quant_hub.services.hedge import (
    Candidate,
    HedgeDataError,
    HedgeService,
    v1_candidates,
)
from quant_hub.services.option_chains import ChainService
from quant_hub.storage.bar_store import BarStore, InstrumentKey
from quant_hub.storage.chain_store import ChainStore
from quant_hub.storage.series_store import SeriesStore
from quant_hub.models import hedge as H

NOW = datetime(2026, 11, 4, 12, 0)
TRUTH = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)


def stored(store, source, name, dates, values, unit="fraction"):
    meta = SeriesMeta(source, name, name, unit, "daily", "made up")
    store.save([FetchedSeries(meta, tuple(d.strftime("%Y-%m-%d") for d in dates),
                              tuple(float(v) for v in values))], NOW, "h")


@pytest.fixture
def world():
    """French's names from 1985, FRED's VIX from 1990 (in vol points)."""
    series, chains, bars = SeriesStore(":memory:"), ChainStore(":memory:"), BarStore(":memory:")
    dates = pd.bdate_range("1985-01-02", "1995-12-29")
    rng = np.random.default_rng(4)
    excess = rng.normal(0.0003, 0.01, len(dates))
    excess[1500:1560] -= 0.006                          # a slide, after VIX starts
    stored(series, "french", "ff3.mkt_rf", dates, excess)
    stored(series, "french", "ff3.rf", dates, np.full(len(dates), 0.0002))
    vix_days = dates[dates >= "1990-01-02"]
    stored(series, "fred", "vixcls", vix_days, np.full(len(vix_days), 20.0), unit="percent")
    service = HedgeService(ExternalDataService(series), ChainService([], chains), bars,
                           now=lambda: NOW)
    yield service, series, chains, bars, dates
    for store in (series, chains, bars):
        store.close()


# ------------------------------------------------------------------ books

def test_the_proxy_book_is_french_at_beta_with_vix_aligned(world):
    service, series, *_ , dates = world
    book = service.proxy_book(0.8)
    x = series.series("french", "ff3.mkt_rf").values
    r = series.series("french", "ff3.rf").values
    assert book.returns.to_numpy() == pytest.approx((r + 0.8 * x).to_numpy())
    assert book.market.to_numpy() == pytest.approx((x + r).to_numpy())
    assert book.vix.loc[:"1989-12-29"].isna().all() and book.vix.loc["1990-01-02"] == 0.20
    assert book.spot.iloc[-1] == pytest.approx(100 * np.prod(1 + (x + r).to_numpy()))


def test_missing_data_is_named_never_fetched():
    empty = HedgeService(ExternalDataService(SeriesStore(":memory:")),
                         ChainService([], ChainStore(":memory:")), BarStore(":memory:"))
    with pytest.raises(HedgeDataError, match="Fetch Ken French factors"):
        empty.proxy_book(1.0)
    with pytest.raises(HedgeDataError, match="sync it on Markets"):
        empty.stream("TLT")
    shape = empty.shape()
    assert (shape.rho, shape.half_spread, shape.atm30) == (1.0, 0.0, None)
    assert "no stored SPX or SPY chain" in shape.label


def test_option_candidates_run_on_vixs_window_and_the_rest_on_the_books(world):
    service, *_ = world
    book = service.proxy_book(1.0)
    put = Candidate("Put 10% OTM, 3 mo", "option", H.OptionProgram(0.9, 91))
    run = service.run(book, [put, Candidate("Cash", "cash")], sizes=(0.5,))
    option, cash = run.results
    assert option.first == datetime(1990, 1, 2).date() and option.assessment is not None
    assert option.program.rolls > 20
    assert cash.first == datetime(1985, 1, 2).date()
    no_vix = HedgeService(ExternalDataService(SeriesStore(":memory:")), None, None)
    blind = replace(book, vix=book.vix * np.nan)
    (missing,) = no_vix.run(blind, [put], sizes=(1.0,)).results
    assert missing.assessment is None and "Fetch FRED series" in missing.missing


def test_todays_mix_is_portfolios_replay_and_h_zero_changes_nothing(world):
    from test_review_service import NOW as RNOW, bars, export
    from quant_hub.data.sources import SyntheticSource
    from quant_hub.services.file_import import FileSource
    from quant_hub.services.market_service import MarketService
    from quant_hub.services.portfolio import PortfolioService
    from quant_hub.storage.position_store import PositionStore
    from quant_hub.importers.positions_csv import read_positions_csv
    import tempfile
    from pathlib import Path
    service, *_ = world
    store_of_bars, _ = bars()
    portfolio = PortfolioService(MarketService(sources=[SyntheticSource(), FileSource(store_of_bars)]),
                                 PositionStore(":memory:"))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "p.csv"
        path.write_text(export("10/22/2026", {"AAA": 40, "BBB": 20}, 4000), encoding="utf-8")
        snapshot = read_positions_csv(path)
    view = portfolio.analyse(snapshot, None, "file", "SPY", 730, 0.04, now=RNOW)
    book = service.mix_book(view, 0.04)
    replay = view.report.pnl / view.report.equity
    interest = (view.cash / view.report.equity) * 0.04 / 252
    assert (book.returns - interest).to_numpy() == pytest.approx(replay.to_numpy(), abs=1e-15)
    (result,) = service.run(book, [Candidate("Cash", "cash")], sizes=(0.0,)).results
    assert result.assessment.growth == 0.0 and result.assessment.cost == 0.0


# ------------------------------------------------------------------ shape

def test_todays_shape_comes_from_the_stored_index_surface(world):
    service, _, chains, *_ = world
    chain = synthetic_chain(TRUTH, SyntheticChainSpec(noise=0.0, parity_pairs=5))
    chains.save(replace(chain, symbol="SPX"))
    shape = service.shape()
    assert shape.atm30 == pytest.approx(TRUTH.atm_vol(30 / 365), rel=0.02)
    assert shape.rho == pytest.approx(shape.atm30 / 0.20)          # today's VIX: 20
    assert shape.smile(np.array([0.0]), np.array([30 / 365]))[0] == pytest.approx(1.0, abs=0.01)
    k = -0.1
    truth_skew = math.sqrt(TRUTH.slice(30 / 365).total_variance(k) / (30 / 365)) / TRUTH.atm_vol(30 / 365)
    assert shape.smile(np.array([k]), np.array([30 / 365]))[0] == pytest.approx(truth_skew, rel=0.03)
    expected = np.median([(q.ask - q.bid) / (q.ask + q.bid) for q in chain.quotes
                          if q.option_type == "put" and q.strike < chain.spot and q.bid > 0])
    assert shape.half_spread == pytest.approx(expected)
    assert "SPX surface" in shape.label
    assert service.shape(flat=True).smile(np.array([k]), np.array([0.1]))[0] == 1.0


# ---------------------------------------------------------------- streams

def test_a_stream_reads_stored_bars_and_holds_on_its_own_window(world):
    service, _, _, bars_store, dates = world
    days = dates[dates >= "1993-01-04"]
    closes = 100 * np.cumprod(np.full(len(days), 1.0001))
    frame = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                          "volume": 1e6}, index=days)
    bars_store.insert_bars(InstrumentKey("TLT"), "file", "1 day", frame)
    returns = service.stream("tlt")
    assert returns.iloc[0] == pytest.approx(0.0001)
    (hold,) = service.run(service.proxy_book(1.0), v1_candidates(["TLT"])[-1:], sizes=(0.3,)).results
    assert hold.candidate.name == "Hold TLT" and hold.first == days[1].date()


def test_the_v1_list_is_decision_2s():
    names = [c.name for c in v1_candidates(["xyz"])]
    assert len(names) == 9 + 2 + 1 + 2 + 1 + 1 + 1
    assert names[0] == "Put 5% OTM, 1 mo" and names[-1] == "Hold XYZ"


# ------------------------------------------------------------------ today

def test_todays_cost_and_its_percentile_among_the_window(world):
    service, _, chains, *_ = world
    book = service.proxy_book(1.0)
    put = Candidate("Put 10% OTM, 3 mo", "option", H.OptionProgram(0.9, 91))
    flat = service.shape()                                          # no chain: flat, ρ = 1
    cost = service.today(book, put, flat)
    expected = H.roll_cost(put.program, np.array([math.log1p(0.0002) * 252]), 0.0,
                           np.array([0.20]))[0]
    assert cost.annual == pytest.approx(expected) and cost.percentile is None
    chains.save(replace(synthetic_chain(TRUTH, SyntheticChainSpec(noise=0.0, parity_pairs=5)),
                        symbol="SPX"))
    shape = service.shape()
    priced = service.today(book, put, shape)
    # VIX was 20 every day, so every past cost is the same: today's, at ρ·VIX = σ_ATM.
    assert priced.percentile == pytest.approx(0.5)
    with pytest.raises(ValueError, match="put or a put spread"):
        service.today(book, Candidate("Collar", "option", H.OptionProgram(0.9, 91, collar=True)),
                      shape)
