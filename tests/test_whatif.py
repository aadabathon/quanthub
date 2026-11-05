"""What-if vectors (ALLOCATION A3, models/whatif.py): typed weights read or
refused by line, a transient snapshot that's never stored, the builders
(a picks total, an index and cash, picks netted into a core at a target
β), and the references against their two-asset closed forms (minimum
variance, equal risk as inverse vol, the tangency portfolio), long-only
and on the vector's invested share. Made-up symbols and returns."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.positions import Holding
from quant_hub.models import whatif as W


def returns(n=3_000, seed=3, vols=(0.010, 0.020), rho=0.3, means=(0.0004, 0.0006)):
    rng = np.random.default_rng(seed)
    cov = np.array([[vols[0] ** 2, rho * vols[0] * vols[1]],
                    [rho * vols[0] * vols[1], vols[1] ** 2]])
    x = rng.multivariate_normal(means, cov, n)
    return pd.DataFrame(x, columns=["AAA", "BBB"], index=pd.bdate_range("2020-01-01", periods=n))


# ------------------------------------------------------------------ vectors

def test_typed_weights_are_read_one_a_line():
    assert W.parse("SPY 60\n# a comment\n\nqqq: 12.5%\nIEF,20\n") == {
        "SPY": 0.6, "QQQ": 0.125, "IEF": 0.2}
    assert W.cash({"SPY": 0.6, "QQQ": 0.125, "IEF": 0.2}) == pytest.approx(0.075)


@pytest.mark.parametrize("text, message", [
    ("SPY sixty", "line 1: write a symbol and its percent"),
    ("SPY 60\nSPY 10", "line 2: SPY appears twice"),
    ("SPY 70\nQQQ 40", "add to 110.0%"),
    ("SPY 120", "between 0 and 100%"),
])
def test_a_vector_that_cant_be_read_says_where(text, message):
    with pytest.raises(ValueError, match=message):
        W.parse(text)


def test_a_snapshot_values_each_weight_and_the_rest_is_cash():
    snap = W.to_snapshot({"aaa": 0.3, "BBB": 0.5}, "test", datetime(2026, 11, 4))
    by = {p.symbol: p for p in snap.positions}
    assert by["AAA"].market_value == pytest.approx(30_000) and by["AAA"].kind is Holding.EQUITY
    assert by["CASH"].kind is Holding.CASH and by["CASH"].market_value == pytest.approx(20_000)
    assert sum(p.market_value for p in snap.positions) == pytest.approx(W.NOTIONAL)
    assert (snap.source, snap.name) == ("What-if", "test")
    with pytest.raises(ValueError):
        W.to_snapshot({}, "empty", datetime(2026, 11, 4))


# ----------------------------------------------------------------- builders

def test_picks_scale_to_a_total_and_the_core_stays():
    out = W.scale_picks({"CORE": 0.6, "AAA": 0.1, "BBB": 0.3}, {"core"}, 0.2)
    assert out == {"CORE": 0.6, "AAA": pytest.approx(0.05), "BBB": pytest.approx(0.15)}
    with pytest.raises(ValueError, match="no picks"):
        W.scale_picks({"CORE": 0.6}, {"CORE"}, 0.2)
    with pytest.raises(ValueError, match="leverage"):
        W.scale_picks({"CORE": 0.9, "AAA": 0.1}, {"CORE"}, 0.3)


def test_netting_lands_the_book_on_the_target_beta():
    picks, betas = {"AAA": 0.1, "BBB": 0.15}, {"AAA": 1.5, "BBB": 1.2}
    out = W.net_into_core(picks, betas, "spy", 1.0, 0.9)
    assert sum(w * (1.0 if s == "SPY" else betas[s]) for s, w in out.items()) == pytest.approx(0.9)
    assert out["SPY"] == pytest.approx(0.9 - 0.15 - 0.18)
    with pytest.raises(ValueError, match="above the target"):
        W.net_into_core(picks, betas, "SPY", 1.0, 0.2)
    with pytest.raises(ValueError, match="leverage"):
        W.net_into_core(picks, betas, "SPY", 0.5, 1.0)
    assert W.index_and_cash("spy", 0.7) == {"SPY": 0.7}


# --------------------------------------------------------------- references

def test_minimum_variance_matches_the_two_asset_closed_form():
    r = returns()
    cov = np.cov(r.to_numpy(), rowvar=False, ddof=1)
    w1 = (cov[1, 1] - cov[0, 1]) / (cov[0, 0] + cov[1, 1] - 2 * cov[0, 1])
    out = W.minimum_variance(r, ["AAA", "BBB"], 0.8)
    assert out["AAA"] == pytest.approx(0.8 * w1, abs=1e-5)
    assert sum(out.values()) == pytest.approx(0.8)


def test_equal_risk_is_inverse_vol_for_two_assets():
    r = returns(rho=0.6)
    vol = r.std(ddof=1).to_numpy()
    out = W.equal_risk(r, ["AAA", "BBB"], 1.0)
    expected = (1 / vol) / (1 / vol).sum()
    assert [out["AAA"], out["BBB"]] == pytest.approx(expected.tolist(), abs=1e-5)


def test_max_sharpe_is_the_tangency_portfolio():
    r = returns(means=(0.0005, 0.0007))
    x = r.to_numpy()
    cov = np.cov(x, rowvar=False, ddof=1)
    tangency = np.linalg.solve(cov, x.mean(axis=0) - 0.0)
    tangency = tangency / tangency.sum()
    out = W.max_sharpe(r, ["AAA", "BBB"], 1.0, rate=0.0)
    assert [out["AAA"], out["BBB"]] == pytest.approx(tangency.tolist(), abs=1e-4)


def test_every_reference_is_long_only_on_the_invested_share():
    rng = np.random.default_rng(8)
    names = ["A", "B", "C", "D"]
    r = pd.DataFrame(rng.normal(0.0003, 0.01, (1_000, 4)) + rng.normal(0, 0.006, (1_000, 1)),
                     columns=names, index=pd.bdate_range("2021-01-01", periods=1_000))
    for out in (W.minimum_variance(r, names, 0.7), W.equal_risk(r, names, 0.7),
                W.equal_weight(names, 0.7), W.max_sharpe(r, names, 0.7, 0.04)):
        assert all(w >= 0 for w in out.values()) and sum(out.values()) == pytest.approx(0.7)
    with pytest.raises(ValueError, match="no returns for E"):
        W.minimum_variance(r, ["A", "E"], 1.0)
