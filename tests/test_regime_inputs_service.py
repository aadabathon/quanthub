"""REGIME_MODEL Q1's service: the inputs from the stored series, and what's
left out when a series hasn't been fetched."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.series import SeriesMeta, StoredSeries
from quant_hub.models.regime_inputs import excess_cape_yield, walk_forward
from quant_hub.services.regime_inputs import BILLS, MARKET, SHILLER, load_regime_inputs
from tests.test_regime_inputs import market_world, shiller_world


class Store:
    """``ExternalDataService.series`` over plain series; a missing one raises
    the service's own LookupError."""

    def __init__(self, series: dict[tuple[str, str], pd.Series]) -> None:
        self._series = series

    def series(self, source: str, series: str) -> StoredSeries:
        if (source, series) not in self._series:
            raise LookupError(f"{source}/{series} hasn't been fetched yet (Ctrl+K: …)")
        meta = SeriesMeta(source, series, series, "fraction", "daily", "cited",
                          note="CRSP 202609" if source == "french" else "")
        return StoredSeries(meta, datetime(2026, 11, 5), "0" * 64, self._series[source, series])


def stored(french=True, shiller=True) -> Store:
    series = {}
    if french:
        total, bills = market_world(years=6)
        series[MARKET], series[BILLS] = total - bills, bills
    if shiller:
        world = shiller_world()
        series.update({("shiller", sid): world[key] for key, sid in SHILLER.items()})
    return Store(series)


DAILY = {"volatility.realized_21d", "trend.excess_12m", "trend.average_200d"}
WALKED = {"volatility.garch", "turbulence.calm"}
MONTHLY = {"valuation.cape", "valuation.ecy", "correlation.stock_bond"}


def test_every_input_from_the_stored_series():
    store = stored()
    found = load_regime_inputs(store, refit_years=2)
    assert {i.name for i in found.inputs} == DAILY | WALKED | MONTHLY
    assert not found.missing
    assert "french/ff3.mkt_rf (CRSP 202609)" in found.sources
    assert "shiller/sp.earnings" in found.sources

    # The market is French's excess plus the bill: the walk-forward on it.
    total = store.series(*MARKET).values + store.series(*BILLS).values
    walk = walk_forward(total, years=2)
    assert found.walk.refits == walk.refits
    assert found.get("volatility.garch").raw.equals(walk.garch_vol)
    world = shiller_world()
    assert found.get("valuation.ecy").raw.equals(excess_cape_yield(
        world["price"], world["earnings"], world["cpi"], world["gs10"]))
    assert found.get("valuation.ecy").frequency == "monthly"

    days = total.index[-5:]
    raw = found.on_days(days, scale="raw")
    assert list(raw.columns) == [i.name for i in found.inputs]
    assert raw.notna().all().all()                           # every input known by then
    # Six years is short of the 1,260 days a daily percentile waits for,
    # except for the inputs that start on the first days.
    ranks = found.on_days(days)
    assert ranks["volatility.realized_21d"].notna().all()
    assert ranks["volatility.garch"].isna().all()
    assert ranks["valuation.ecy"].between(0, 1).all()
    with pytest.raises(ValueError, match="scale"):
        found.on_days(days, scale="rank")
    with pytest.raises(KeyError, match="credit"):
        found.get("credit.spread")


def test_without_shillers_data_the_monthly_inputs_say_how_to_get_it():
    found = load_regime_inputs(stored(shiller=False), walk=False)
    assert {i.name for i in found.inputs} == DAILY            # no refits asked for
    assert found.walk is None
    (why,) = found.missing
    assert why.startswith("Valuation and the stock–bond correlation need Shiller's data")


def test_without_frenchs_factors_only_the_monthly_inputs():
    found = load_regime_inputs(stored(french=False))
    assert {i.name for i in found.inputs} == MONTHLY
    assert found.missing[0].startswith("The daily inputs need Ken French's factors")
    assert np.isfinite(found.get("correlation.stock_bond").raw).all()
