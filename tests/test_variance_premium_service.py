"""The variance premium wired through the vol context (VARIANCE I2): stored
chains, another symbol's bars, today's fit and the history's quote sums."""

import math
from dataclasses import replace
from datetime import date, datetime, time

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.trading_calendar import trading_days
from quant_hub.data.types import PriceBasis, PriceHistory
from quant_hub.models.carry import implied_carry
from quant_hub.models.implied_variance import quote_tenor
from quant_hub.models.svi import SSVISurface
from quant_hub.models.variance_premium import window_after
from quant_hub.models.vol_surface import SyntheticChainSpec, synthetic_chain
from quant_hub.services.market_service import MarketService
from quant_hub.services.option_chains import ChainService
from quant_hub.services.vol_context import load_variance_premium
from quant_hub.storage.chain_store import ChainStore

NOW = datetime(2025, 11, 14, 12)
SURFACE = SSVISurface(v0=0.17**2, v_inf=0.18**2, kappa=2.0, rho=-0.7, eta=1.0)
SNAPSHOT_DAYS = trading_days(date(2025, 6, 2), date(2025, 10, 31))


class PathSource:
    """Daily closes on the NYSE's trading days, through the session at NOW."""

    name = "path"

    def __init__(self, closes: pd.Series) -> None:
        self._closes = closes

    def get_history(self, symbol, start, end, basis=PriceBasis.TRADED) -> PriceHistory:
        window = self._closes[(self._closes.index >= start) & (self._closes.index <= end)]
        return PriceHistory(symbol, self.name, window.to_frame("close"), basis)

    def get_quote(self, symbol):
        raise NotImplementedError


def gbm(seed=1, split_on: date | None = None) -> pd.Series:
    days = trading_days(date(2021, 1, 4), NOW.date())          # includes NOW's open session
    rng = np.random.default_rng(seed)
    sd = 0.16 / math.sqrt(252)
    closes = 400 * np.exp(np.cumsum(rng.normal(-sd**2 / 2, sd, len(days))))
    if split_on is not None:
        closes[[d >= split_on for d in days]] /= 2                 # an unadjusted 2:1 split
    return pd.Series(closes, index=pd.DatetimeIndex(days))


@pytest.fixture
def world():
    store = ChainStore(":memory:")
    for i, day in enumerate(SNAPSHOT_DAYS):
        spec = SyntheticChainSpec(seed=i, k_range_sd=(-6, 3), strikes_per_expiry=31)
        store.save(synthetic_chain(SURFACE, spec, as_of=datetime.combine(day, time(10))))
    # A second snapshot late on the last day, and another symbol's chain.
    late = synthetic_chain(SURFACE, SyntheticChainSpec(seed=999, k_range_sd=(-6, 3)),
                           as_of=datetime.combine(SNAPSHOT_DAYS[-1], time(15, 30)))
    store.save(late)
    other = synthetic_chain(SURFACE, SyntheticChainSpec(seed=5), as_of=datetime(2025, 7, 1, 10))
    store.save(replace(other, symbol="OTHER"))
    yield ChainService([], store), store
    store.close()


def load(chains, closes, **kwargs):
    market = MarketService([PathSource(closes)])
    return load_variance_premium(market, chains, "synth", "path", realized_symbol="spy",
                                 now=NOW, **kwargs)


def test_todays_premium_and_the_history(world):
    chains, store = world
    view = load(chains, gbm())
    assert (view.symbol, view.realized_symbol, view.source) == ("SYNTH", "SPY", "path")
    assert view.as_of == datetime.combine(SNAPSHOT_DAYS[-1], time(15, 30))   # the newest
    assert view.days_with_snapshots == len(SNAPSHOT_DAYS)
    assert [e.dte for e in view.expiries] == [7, 30, 60, 91, 182, 365]
    for e in view.expiries:
        assert e.trading_days == len(window_after(SNAPSHOT_DAYS[-1], e.dte))
        assert e.garch.years == pytest.approx(e.dte / 365)
        assert e.implied.extrapolated_share < 0.05              # the wide band
    assert view.tenor.days == 30 and view.garch_tenor.years == pytest.approx(30 / 365)
    assert view.garch_tenor.implied == pytest.approx(view.tenor.smile.total)

    # The history is each day's first snapshot, valued by its quote sums.
    first = {s.as_of.date(): s.id for s in sorted(store.snapshots("SYNTH"),
                                                   key=lambda s: s.as_of, reverse=True)}
    for p in view.trailing_history.points:
        expected = quote_tenor(implied_carry(store.load(first[p.as_of.date()])).chain, 30)
        assert p.premium.implied == pytest.approx(expected.mid, rel=1e-12)
    assert len(view.trailing_history.points) == len(SNAPSHOT_DAYS)
    assert view.trailing == view.trailing_history.points[-1].premium
    assert view.quote_tenor.mid == pytest.approx(view.trailing.implied)
    assert view.trailing_percentile is not None and 0 <= view.trailing_percentile <= 1

    # Ex post: only windows whose last session has closed (NOW's is still open).
    known = [d for d in SNAPSHOT_DAYS if window_after(d, 30)[-1] < NOW.date()]
    assert len(view.ex_post.points) == len(known)
    assert view.ex_post.pending == len(SNAPSHOT_DAYS) - len(known)
    assert view.ex_post.summary() is not None
    assert not view.notes and not view.split_gaps


def test_a_split_sized_gap_is_named_and_its_windows_left_out(world):
    chains, _ = world
    view = load(chains, gbm(split_on=date(2025, 7, 15)))
    assert [g.day.date() for g in view.split_gaps] == [date(2025, 7, 15)]
    assert any("split-sized gap" in n for n in view.notes)
    reasons = {e.reason for e in view.trailing_history.excluded + view.ex_post.excluded}
    assert reasons == {"a split-sized gap on 2025-07-15 (D57)"}
    assert all(date(2025, 7, 15) not in p.window
               for p in view.trailing_history.points + view.ex_post.points)


def test_no_stored_chains_says_how_to_get_them(world):
    chains, _ = world
    with pytest.raises(ValueError, match=r"no stored QQQ chains yet.*\[collect\] chains"):
        load_variance_premium(MarketService([PathSource(gbm())]), chains, "qqq", "path",
                              now=NOW)
