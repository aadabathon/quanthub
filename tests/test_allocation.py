"""The core and the rest (ALLOCATION A1, models/allocation.py): the book's β
splits exactly into the core's and the rest's, the core weight for a target
β lands the book on it, and every input that can't be netted is refused."""

import numpy as np
import pytest

from quant_hub.models.allocation import core_netting


def sample_beta(r: np.ndarray, m: np.ndarray) -> float:
    return float(np.cov(r, m, ddof=1)[0, 1] / np.var(m, ddof=1))


def test_the_books_beta_is_the_weighted_sum_of_its_names_betas_exactly():
    # The docstring's claim: OLS β is linear in the dependent series, and
    # cash (a constant daily return) adds nothing. Checked on sample βs.
    rng = np.random.default_rng(7)
    m = rng.normal(0.0004, 0.011, 750)
    names = {"CORE": 1.0, "P1": 1.6, "P2": 0.4, "P3": -0.3}
    returns = {n: b * m + rng.normal(0, 0.015, m.size) for n, b in names.items()}
    weights = {"CORE": 0.55, "P1": 0.12, "P2": 0.08, "P3": 0.05}
    cash = (1 - sum(weights.values())) * 0.04 / 252
    book = sum(w * returns[n] for n, w in weights.items()) + cash
    betas = {n: sample_beta(returns[n], m) for n in names}
    netting = core_netting(weights, betas, {"CORE"})
    assert netting.beta == pytest.approx(sample_beta(book, m), rel=1e-12)
    assert netting.rest_beta == pytest.approx(sum(weights[n] * betas[n] for n in ("P1", "P2", "P3")))


def test_the_core_weight_for_a_band_lands_the_book_on_each_side():
    weights = {"CORE": 0.5, "A": 0.1, "B": 0.1}
    betas = {"CORE": 0.9, "A": 1.5, "B": 1.2}
    n = core_netting(weights, betas, {"CORE"}, band=(0.8, 1.0))
    assert n.rest_beta == pytest.approx(0.27) and n.core_beta == pytest.approx(0.9)
    for target, w_core in zip(n.band, n.core_for_band):
        assert n.rest_beta + w_core * n.core_beta == pytest.approx(target)
    assert n.core_for_band == pytest.approx(((0.8 - 0.27) / 0.9, (1.0 - 0.27) / 0.9))
    assert n.room == pytest.approx(0.8)


def test_a_benchmark_core_nets_one_for_one_and_a_one_sided_band_keeps_its_blank():
    n = core_netting({"SPY": 0.6, "A": 0.2}, {"SPY": 1.0, "A": 1.5}, {"SPY", "NOT_HELD"},
                     band=(None, 0.9))
    assert n.core == ("SPY",)
    assert n.core_for_band[0] is None and n.core_for_band[1] == pytest.approx(0.9 - 0.3)


def test_several_core_holdings_net_as_one_unit():
    n = core_netting({"X": 0.3, "Y": 0.1, "A": 0.2}, {"X": 1.0, "Y": 0.6, "A": 1.0}, {"X", "Y"})
    assert n.core_weight == pytest.approx(0.4)
    assert n.core_beta == pytest.approx((0.3 * 1.0 + 0.1 * 0.6) / 0.4)
    assert n.band is None and n.core_for_band is None


@pytest.mark.parametrize("weights, betas, core, message", [
    ({"A": 0.5}, {"A": 1.0}, {"SPY"}, "no core holding"),
    ({"SPY": -0.2, "A": 0.5}, {"SPY": 1.0, "A": 1.0}, {"SPY"}, "weight isn't positive"),
    ({"SPY": 0.5, "A": 0.5}, {"SPY": -0.1, "A": 1.0}, {"SPY"}, "isn't positive"),
    ({"SPY": 0.5}, {"SPY": 1.0, "A": 1.0}, {"SPY"}, "same names"),
    ({"SPY": 0.5, "A": float("nan")}, {"SPY": 1.0, "A": 1.0}, {"SPY"}, "finite"),
])
def test_what_cant_be_netted_is_refused(weights, betas, core, message):
    with pytest.raises(ValueError, match=message):
        core_netting(weights, betas, core)


@pytest.mark.parametrize("band, reachable", [
    ((0.5, 1.5), (0.5 - 0.3, 0.7)),          # the top needs leverage: capped at the room
    ((None, 0.9), (0.0, 0.6)),               # no floor: from zero
    ((0.2, 0.9), (0.0, 0.6)),                # the rest alone clears the floor
    ((0.1, 0.2), None),                      # the rest alone is above the top
    ((1.2, 1.5), None),                      # even the floor needs leverage
])
def test_the_weights_reachable_without_leverage(band, reachable):
    n = core_netting({"SPY": 0.5, "A": 0.3}, {"SPY": 1.0, "A": 1.0}, {"SPY"}, band=band)
    assert n.room == pytest.approx(0.7) and n.rest_beta == pytest.approx(0.3)
    assert n.reachable() == (pytest.approx(reachable) if reachable else None)
