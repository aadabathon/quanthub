"""The variance premium wired through the vol context (VARIANCE I2, I4): a
fitted chain, the stored chains of its symbol, another symbol's bars, and the
history's quote sums."""

import math
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.trading_calendar import trading_days
from quant_hub.data.types import PriceBasis, PriceHistory
from quant_hub.models.carry import implied_carry
from quant_hub.models.implied_variance import quote_tenor
from quant_hub.models.svi import SSVISurface
from quant_hub.models.variance_premium import (
    closes_by_day,
    garch_expected_total,
    realized_total,
    window_after,
    window_before,
)
from quant_hub.models.vol_surface import (
    SyntheticChainSpec,
    fit_surface,
    synthetic_chain,
)
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


def newest_fit(store, symbol="SYNTH"):
    newest = max(store.snapshots(symbol), key=lambda s: (s.as_of, s.id))
    return fit_surface(implied_carry(store.load(newest.id)).chain)


def load(chains, closes, fit, **kwargs):
    market = MarketService([PathSource(closes)])
    return load_variance_premium(market, chains, fit, "path", realized_symbol="spy",
                                 now=NOW, **kwargs)


def test_todays_premium_and_the_history(world):
    chains, store = world
    closes = gbm()
    fit = newest_fit(store)
    view = load(chains, closes, fit)
    assert (view.symbol, view.realized_symbol, view.source) == ("SYNTH", "SPY", "path")
    day0 = SNAPSHOT_DAYS[-1]
    assert view.as_of == datetime.combine(day0, time(15, 30))   # the fitted chain's
    assert view.days_with_snapshots == len(SNAPSHOT_DAYS)
    assert [e.dte for e in view.expiries] == [7, 30, 60, 91, 182, 365]
    by_day = closes_by_day(closes)
    for e in view.expiries:
        assert e.trading_days == len(window_after(day0, e.dte))
        assert e.garch.years == e.trailing.years == pytest.approx(e.dte / 365)
        assert e.garch.implied == e.trailing.implied == pytest.approx(e.implied.total)
        assert e.trailing.against == pytest.approx(                 # the same length, before
            realized_total(by_day, window_before(day0, e.dte)), rel=1e-12)
        assert e.implied.extrapolated_share < 0.05              # the wide band
    assert view.tenor.days == 30 and view.garch_tenor.years == pytest.approx(30 / 365)
    assert view.garch_tenor.implied == pytest.approx(view.tenor.smile.total)

    # One GARCH fit, as of the chain's day, behind the columns and the overlay.
    context = view.context
    assert context.history_end.date() == day0 and context.symbol == "SPY"
    assert context.returns_used == sum(
        (day0 - timedelta(days=1825)) <= d <= day0 for d in by_day) - 1
    first = view.expiries[0]
    assert first.garch.against == pytest.approx(garch_expected_total(
        context.garch, context.next_variance, first.trading_days), rel=1e-12)

    # The history is each day's first snapshot, valued by its quote sums.
    first = {s.as_of.date(): s.id for s in sorted(store.snapshots("SYNTH"),
                                                   key=lambda s: s.as_of, reverse=True)}
    for p in view.trailing_history.points:
        expected = quote_tenor(implied_carry(store.load(first[p.as_of.date()])).chain, 30)
        assert p.premium.implied == pytest.approx(expected.mid, rel=1e-12)
    assert len(view.trailing_history.points) == len(SNAPSHOT_DAYS)
    # Today's is the fitted chain's quote sum (the late snapshot), over the
    # same trailing window as the day's first.
    assert view.quote_tenor.mid == pytest.approx(quote_tenor(fit.chain, 30).mid, rel=1e-12)
    assert view.trailing.implied == pytest.approx(view.quote_tenor.mid)
    assert view.trailing.against == view.trailing_history.points[-1].premium.against
    assert view.trailing_percentile is not None and 0 <= view.trailing_percentile <= 1

    # Ex post: only windows whose last session has closed (NOW's is still open).
    known = [d for d in SNAPSHOT_DAYS if window_after(d, 30)[-1] < NOW.date()]
    assert len(view.ex_post.points) == len(known)
    assert view.ex_post.pending == len(SNAPSHOT_DAYS) - len(known)
    assert view.ex_post.summary() is not None
    assert not view.notes and not view.split_gaps


def test_a_split_sized_gap_is_named_and_its_windows_left_out(world):
    chains, store = world
    view = load(chains, gbm(split_on=date(2025, 7, 15)), newest_fit(store))
    assert [g.day.date() for g in view.split_gaps] == [date(2025, 7, 15)]
    assert any("split-sized gap" in n for n in view.notes)
    reasons = {e.reason for e in view.trailing_history.excluded + view.ex_post.excluded}
    assert reasons == {"a split-sized gap on 2025-07-15 (D57)"}
    assert all(date(2025, 7, 15) not in p.window
               for p in view.trailing_history.points + view.ex_post.points)


def test_a_chain_with_no_stored_history_is_still_priced(world):
    # A synthetic chain on the page, say: today's premiums, no history.
    chains, _ = world
    chain = synthetic_chain(SURFACE, SyntheticChainSpec(seed=3), as_of=datetime(2025, 11, 3, 11))
    view = load(chains, gbm(), fit_surface(replace(chain, symbol="QQQ")))
    assert view.symbol == "QQQ" and view.days_with_snapshots == 0
    assert all(e.garch is not None and e.trailing is not None for e in view.expiries)
    assert view.trailing is not None and view.trailing_percentile is None
    assert not view.ex_post.points and not view.trailing_history.points
    assert any("No stored QQQ chains" in n and "[collect] chains" in n for n in view.notes)


def test_history_off_skips_the_stored_snapshots(world):
    # The lab's synthetic chain: no lookup, so no advice about collecting it.
    chains, store = world
    view = load(chains, gbm(), newest_fit(store), history=False)
    assert view.days_with_snapshots == 0 and not view.trailing_history.points
    assert not any("No stored" in n for n in view.notes)
    assert view.trailing is not None and view.context is not None


def test_without_bars_there_is_nothing_to_compare(world):
    # Bars that end before the windows: GARCH and the trailing windows say
    # why, and the implied side still shows.
    chains, store = world
    closes = gbm()
    view = load(chains, closes[closes.index < pd.Timestamp(2021, 1, 6)], newest_fit(store))
    assert [e.dte for e in view.expiries] == [7, 30, 60, 91, 182, 365]
    assert all(e.garch is None and e.trailing is None for e in view.expiries)
    assert view.garch_tenor is None and view.trailing is None and view.context is None
    assert any(n.startswith("No GARCH comparison") for n in view.notes)
    assert any(n.startswith("No trailing realized for 7d, 30d") for n in view.notes)


def test_the_realized_side_needs_a_symbol(world):
    chains, store = world
    with pytest.raises(ValueError, match="realized side"):
        load_variance_premium(MarketService([PathSource(gbm())]), chains, newest_fit(store),
                              "path", realized_symbol=" ", now=NOW)
