"""Crash stress (ALLOCATION A2, models/stress.py): β = 1 is the market's own
fall, β = 0 never falls, the fall grows with |β| (the docstring's
concavity argument), a planted crash is found with its dates, the band
brackets the estimate, and a wipe-out stays wiped out. Made-up returns."""

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.stress import Z95, crash_stress, falls, replay, worst_fall


def market(n=2_000, seed=3, crash_at=600, crash_days=120):
    """Made-up daily excess returns with a planted slide, and steady bills."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("1990-01-02", periods=n)
    x = rng.normal(0.0004, 0.01, n)
    x[crash_at:crash_at + crash_days] -= 0.006               # about −50% over the slide
    rf = np.full(n, 0.04 / 252)
    return pd.Series(x, index=dates), pd.Series(rf, index=dates)


def drawdown_of(levels: np.ndarray) -> float:
    peaks = np.maximum.accumulate(np.concatenate(([1.0], levels)))[1:]
    return float(-(levels / peaks - 1).min())


def test_beta_one_is_the_markets_own_fall():
    x, rf = market()
    total = np.cumprod(1 + x.to_numpy() + rf.to_numpy())     # the market's total return
    assert worst_fall(x, rf, 1.0) == pytest.approx(drawdown_of(total))
    assert replay(x, rf, 1.0).to_numpy() == pytest.approx(total)


def test_beta_zero_holds_bills_and_never_falls():
    x, rf = market()
    stress = crash_stress(x, rf, 0.0)
    assert stress.worst.depth == 0.0 and (stress.drawdown == 0).all()
    assert stress.path.iloc[-1] == pytest.approx((1 + 0.04 / 252) ** len(x))


def test_the_fall_grows_with_the_size_of_beta_either_side_of_zero():
    x, rf = market(seed=11)
    up = [worst_fall(x, rf, b) for b in np.linspace(0, 3, 31)]
    down = [worst_fall(x, rf, b) for b in np.linspace(0, -3, 31)]
    assert all(b >= a - 1e-12 for a, b in zip(up, up[1:]))
    assert all(b >= a - 1e-12 for a, b in zip(down, down[1:]))


def test_planted_crashes_are_found_worst_first_with_their_dates():
    # A steady market with two slides: 120 days at −0.6% and, after it has
    # recovered (about 1,450 days), 30 at −0.3%.
    dates = pd.bdate_range("1990-01-02", periods=3_000)
    x = np.full(len(dates), 0.0005)
    x[600:720] = -0.006
    x[2400:2430] = -0.003
    x, rf = pd.Series(x, index=dates), pd.Series(0.0, index=dates)
    stress = crash_stress(x, rf, 1.0)
    big, small = stress.falls
    assert big.depth == pytest.approx(1 - 0.994 ** 120)
    assert (big.peak, big.trough) == (dates[599].date(), dates[719].date())
    climb = int(np.ceil(np.log(1 / 0.994 ** 120) / np.log(1.0005)))     # days back to the peak
    assert big.recovered == dates[719 + climb].date()
    assert small.depth == pytest.approx(1 - 0.997 ** 30)
    assert (small.peak, small.trough) == (dates[2399].date(), dates[2429].date())
    assert big.recovered <= small.peak                                # separate spells


def test_a_fall_not_yet_regained_has_no_recovery_and_a_fall_from_the_start_counts():
    dates = pd.bdate_range("2020-01-01", periods=4)
    path = pd.Series([0.9, 0.8, 1.1, 1.0], index=dates)
    drawdown, spells = falls(path)
    assert spells[0].depth == pytest.approx(0.2) and spells[0].peak == dates[0].date()
    assert spells[0].trough == dates[1].date() and spells[0].recovered == dates[2].date()
    assert spells[1].depth == pytest.approx(1 - 1.0 / 1.1) and spells[1].recovered is None
    assert drawdown.iloc[2] == 0.0


def test_the_band_is_the_falls_at_beta_minus_and_plus_z_errors():
    x, rf = market()
    stress = crash_stress(x, rf, 0.8, error=0.05)
    (b_low, d_low), (b_high, d_high) = stress.band
    assert (b_low, b_high) == pytest.approx((0.8 - Z95 * 0.05, 0.8 + Z95 * 0.05))
    assert d_low <= stress.worst.depth <= d_high
    assert d_high == pytest.approx(worst_fall(x, rf, b_high))
    assert crash_stress(x, rf, 0.8).band is None


def test_a_wipe_out_stays_wiped_out():
    dates = pd.bdate_range("1987-10-14", periods=5)
    x = pd.Series([0.0, -0.02, -0.18, 0.05, 0.05], index=dates)
    rf = pd.Series(0.0, index=dates)
    path = replay(x, rf, 6.0)
    assert path.iloc[2:].eq(0.0).all()
    assert crash_stress(x, rf, 6.0).worst.depth == 1.0


@pytest.mark.parametrize("excess, bills, beta, message", [
    (pd.Series([0.01], index=pd.to_datetime(["2020-01-02"])),
     pd.Series([0.0], index=pd.to_datetime(["2021-01-04"])), 1.0, "share no dates"),
    (pd.Series([np.nan], index=pd.to_datetime(["2020-01-02"])),
     pd.Series([0.0], index=pd.to_datetime(["2020-01-02"])), 1.0, "finite"),
    (pd.Series([0.01], index=pd.to_datetime(["2020-01-02"])),
     pd.Series([0.0], index=pd.to_datetime(["2020-01-02"])), float("inf"), "β must be finite"),
])
def test_what_cant_be_replayed_is_refused(excess, bills, beta, message):
    with pytest.raises(ValueError, match=message):
        replay(excess, bills, beta)
