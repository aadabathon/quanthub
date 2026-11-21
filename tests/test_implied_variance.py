"""Model-free implied variance (VARIANCE I1): the module docstring's
validation plan, against planted truths."""

import math
from datetime import datetime
from itertools import pairwise

import numpy as np
import pytest

from quant_hub.data.options import OptionChain, OptionQuote
from quant_hub.models.implied_variance import (
    HEADLINE_DAYS,
    ExpiryVariance,
    QuoteSum,
    SmileVariance,
    black_call,
    black_put,
    constant_maturity,
    forward_variances,
    implied_variance,
    normal_average,
    quote_sum,
    quote_tenor,
    smile_bounds,
    smile_variance,
    span_total,
)
from quant_hub.models.svi import RawSVI, SSVISurface
from quant_hub.models.vol_surface import (
    SyntheticChainSpec,
    fit_surface,
    synthetic_chain,
)

AS_OF = datetime(2026, 11, 5, 15, 0)
SURFACE = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)


def ssvi_slice(atm: float, rho: float, eta: float, days: float) -> RawSVI:
    """The plan's slice: SSVI at one expiry, γ = ½, built as raw SVI (svi.py's map)."""
    theta = atm**2 * days / 365
    phi = eta / math.sqrt(theta * (1 + theta))
    return RawSVI(a=0.5 * theta * (1 - rho**2), b=0.5 * theta * phi, rho=rho, m=-rho / phi,
                  sigma=math.sqrt(1 - rho**2) / phi)


PLAN_SLICE = ssvi_slice(0.14, -0.7, 1.0, 30)
STEEP_SLICE = ssvi_slice(0.14, -0.7, 1.5, 30)
MONTH = 30 / 365


def exact_chain(params: RawSVI, days: float, strikes, forward: float = 100.0,
                rate: float = 0.04, both_sides=(), spread: float = 0.0) -> OptionChain:
    """Noise-free out-of-the-money quotes off one smile, at the price ± a
    relative half-``spread``; ``both_sides`` strikes are quoted as a parity pair."""
    discount = math.exp(-rate * days / 365)
    quotes = []
    for strike in strikes:
        k = math.log(strike / forward)
        w = float(params.total_variance(k))
        for kind, price in (("put", black_put), ("call", black_call)):
            if (kind == "call") != (strike >= forward) and strike not in both_sides:
                continue
            value = float(discount * forward * price(k, w))
            if value > 0:
                quotes.append(OptionQuote(kind, float(strike), float(days),
                                          value * (1 - spread), value * (1 + spread)))
    return OptionChain("X", "test", AS_OF, forward * discount, rate, 0.0, tuple(quotes))


def sd_strikes(params: RawSVI, low: float, high: float, n: int, forward: float = 100.0):
    sd = math.sqrt(float(params.total_variance(0.0)))
    return forward * np.exp(np.linspace(low * sd, high * sd, n))


# ------------------------------------------------------------ 1. flat smile

@pytest.mark.parametrize("days", [7, 30, 91, 365])
def test_a_flat_smile_gives_sigma_squared_exactly(days):
    years, vol = days / 365, 0.20
    flat = RawSVI(a=vol**2 * years, b=0.0, rho=0.0, m=0.0, sigma=1.0)
    sd = vol * math.sqrt(years)
    smile = smile_variance(flat, years, -2.5 * sd, 1.5 * sd)
    assert smile.total == pytest.approx(vol**2 * years, rel=1e-10)
    assert smile.variance == pytest.approx(vol**2, rel=1e-10)
    assert normal_average(flat) == pytest.approx(vol**2 * years, rel=1e-10)
    assert smile.atm_vol == pytest.approx(vol)
    assert 0 < smile.extrapolated_share < 0.2              # the band misses the wings
    dense = quote_sum(exact_chain(flat, days, sd_strikes(flat, -9, 9, 801)), float(days))
    assert dense.mid == pytest.approx(vol**2 * years, rel=1e-4)


# ------------------------------------------------- 2. the k-form = the z-form

@pytest.mark.parametrize("method", ["svi", "ssvi"])
def test_the_k_form_equals_the_z_form_on_fitted_slices(method):
    fit = fit_surface(synthetic_chain(SURFACE, SyntheticChainSpec(seed=3)), method=method)
    assert fit.butterfly_free
    for s in fit.slices:
        smile = smile_variance(s.fit.params, s.years, s.k_min, s.k_max)
        assert smile.total == pytest.approx(normal_average(s.fit.params), rel=1e-8)
        assert smile.total > smile.atm_total                # a skew lifts it above ATM


def test_the_k_form_equals_the_z_form_on_the_true_surface():
    for days in (7, 30, 365):
        params = SURFACE.slice(days / 365)
        assert smile_variance(params, days / 365, -0.1, 0.1).total == pytest.approx(
            normal_average(params), rel=1e-8)


# --------------------------------------------------- 3. the worked example

def test_the_plans_worked_example():
    sd = math.sqrt(float(PLAN_SLICE.total_variance(0.0)))
    assert math.sqrt(float(PLAN_SLICE.total_variance(0.0)) / MONTH) == pytest.approx(0.14)
    whole = smile_variance(PLAN_SLICE, MONTH, -2.5 * sd, 1.5 * sd)
    assert whole.vol == pytest.approx(0.166, abs=5e-4)
    assert 1 - whole.extrapolated_share == pytest.approx(0.86, abs=0.005)
    assert math.sqrt(whole.quoted / MONTH) == pytest.approx(0.154, abs=5e-4)
    shares = {band: smile_variance(PLAN_SLICE, MONTH, band[0] * sd, band[1] * sd)
              for band in ((-4, 2), (-6, 3))}
    assert 1 - shares[(-4, 2)].extrapolated_share == pytest.approx(0.95, abs=0.005)
    assert 1 - shares[(-6, 3)].extrapolated_share == pytest.approx(0.985, abs=0.005)
    assert math.sqrt(shares[(-6, 3)].quoted / MONTH) == pytest.approx(0.164, abs=5e-4)

    # The VIX sum on 5-point strikes over the wide band: the band's own integral.
    forward = 5000.0
    low, high = forward * math.exp(-6 * sd), forward * math.exp(3 * sd)
    strikes = np.arange(math.ceil(low / 5) * 5, high, 5.0)
    summed = quote_sum(exact_chain(PLAN_SLICE, 30, strikes, forward), 30.0, PLAN_SLICE)
    band = smile_variance(PLAN_SLICE, MONTH, math.log(strikes[0] / forward),
                          math.log(strikes[-1] / forward))
    assert summed.vol == pytest.approx(0.164, abs=5e-4)
    assert summed.mid == pytest.approx(band.quoted, rel=1e-3)
    assert summed.fit == pytest.approx(summed.mid, rel=1e-9)        # the fit is the quotes here

    steep = smile_variance(STEEP_SLICE, MONTH, -2.5 * sd, 1.5 * sd)
    assert steep.vol == pytest.approx(0.193, abs=5e-4)
    assert 1 - steep.extrapolated_share == pytest.approx(0.73, abs=0.005)
    assert steep.atm_vol == pytest.approx(0.14)


# ------------------------------------------------- 4. the synthetic SSVI world

@pytest.mark.parametrize("seed", range(4))
def test_on_the_synthetic_world_the_fit_sits_inside_the_quotes_band(seed):
    shares = {}
    for band, strikes in (((-2.5, 1.5), 21), ((-6, 3), 41)):
        chain = synthetic_chain(SURFACE, SyntheticChainSpec(seed=seed, k_range_sd=band,
                                                            strikes_per_expiry=strikes))
        term = implied_variance(fit_surface(chain))
        assert [e.dte for e in term.expiries] == list(chain.expiries)
        for e in term.expiries:
            q = e.quotes
            assert q is not None and q.inside, (band, e.dte)
            assert q.bid < q.mid < q.ask
            truth = smile_variance(SURFACE.slice(e.years), e.years, -0.1, 0.1).total
            assert abs(e.total - truth) <= (q.ask - q.bid) / 2, (band, e.dte)
            truth_on_strikes = quote_sum(chain, e.dte, SURFACE.slice(e.years)).fit
            assert q.bid <= truth_on_strikes <= q.ask
        shares[band] = max(e.smile.extrapolated_share for e in term.expiries)
        assert term.calendar_free
    assert shares[(-2.5, 1.5)] > 0.15 and shares[(-6, 3)] < 0.05      # decision 2's point


def test_a_fit_outside_the_band_is_flagged():
    chain = exact_chain(PLAN_SLICE, 30, sd_strikes(PLAN_SLICE, -3, 2, 31), spread=0.02)
    assert quote_sum(chain, 30.0, PLAN_SLICE).inside
    lifted = RawSVI(PLAN_SLICE.a * 1.3, PLAN_SLICE.b, PLAN_SLICE.rho, PLAN_SLICE.m,
                    PLAN_SLICE.sigma)
    assert quote_sum(chain, 30.0, lifted).inside is False
    assert quote_sum(chain, 30.0).inside is None


# --------------------------------------------------------------- 5. Merton

MERTON = {"sigma": 0.12, "lam": 0.5, "mu": -0.08, "delta": 0.10, "days": 91}


def merton_price(k: np.ndarray, call: bool) -> np.ndarray:
    """Merton (1976) in units of DF: a Poisson mix of Black prices. Given n
    jumps, ln(S_T/F) is normal with variance σ²T + nδ² and the conditional
    forward is F e^{-λκT}(1+κ)^n, κ = E[e^J] - 1."""
    m = MERTON
    years = m["days"] / 365
    kappa = math.exp(m["mu"] + m["delta"] ** 2 / 2) - 1
    total = np.zeros_like(np.asarray(k, dtype=float))
    for n in range(40):
        weight = math.exp(-m["lam"] * years) * (m["lam"] * years) ** n / math.factorial(n)
        forward_n = math.exp(-m["lam"] * kappa * years) * (1 + kappa) ** n
        variance_n = m["sigma"] ** 2 * years + n * m["delta"] ** 2
        price = black_call if call else black_put
        total += weight * forward_n * price(np.asarray(k) - math.log(forward_n), variance_n)
    return total


def test_on_a_merton_chain_the_log_contract_matches_its_closed_form():
    m = MERTON
    years = m["days"] / 365
    kappa = math.exp(m["mu"] + m["delta"] ** 2 / 2) - 1
    closed = m["sigma"] ** 2 * years + 2 * m["lam"] * years * (kappa - m["mu"])  # -2E[ln S_T/F]
    expected_qv = m["sigma"] ** 2 * years + m["lam"] * years * (m["mu"] ** 2 + m["delta"] ** 2)
    otm = lambda k: np.where(k >= 0, merton_price(k, True), merton_price(k, False))
    spanned = span_total(otm, -4.0, 3.0)
    assert spanned == pytest.approx(closed, rel=1e-6)
    jump_term = m["lam"] * years * (2 * (kappa - m["mu"]) - (m["mu"] ** 2 + m["delta"] ** 2))
    assert spanned - expected_qv == pytest.approx(jump_term, rel=1e-4)
    assert jump_term < 0                                     # below a variance swap rate
    assert math.sqrt(spanned / years) == pytest.approx(0.1488, abs=5e-5)
    assert math.sqrt(expected_qv / years) == pytest.approx(0.1503, abs=5e-5)

    # The quote sum on a dense Merton chain gets there too, model-free.
    forward, rate = 100.0, 0.04
    discount = math.exp(-rate * years)
    quotes = []
    for strike in forward * np.exp(np.linspace(-1.5, 0.8, 600)):
        call = strike >= forward
        value = float(discount * forward * merton_price(np.log(strike / forward), call))
        quotes.append(OptionQuote("call" if call else "put", float(strike), 91.0, value, value))
    chain = OptionChain("M", "test", AS_OF, forward * discount, rate, 0.0, tuple(quotes))
    assert quote_sum(chain, 91.0).mid == pytest.approx(closed, rel=1e-3)


# ------------------------------------------------ 6. densifying, truncating

def test_the_vix_sum_converges_as_strikes_densify():
    errors = []
    for n in (21, 41, 81, 161):
        strikes = sd_strikes(PLAN_SLICE, -8, 4, n)
        summed = quote_sum(exact_chain(PLAN_SLICE, 30, strikes), 30.0)
        band = smile_variance(PLAN_SLICE, MONTH, math.log(strikes[0] / 100),
                              math.log(strikes[-1] / 100)).quoted
        errors.append(abs(summed.mid - band))
    assert all(a / b > 3.5 for a, b in pairwise(errors))       # about h²


def test_truncation_only_lowers_the_sum():
    sd = math.sqrt(float(PLAN_SLICE.total_variance(0.0)))
    k = np.linspace(-8 * sd, 4 * sd, 241)
    sums = []
    for low, high in ((-2.5, 1.5), (-4, 2), (-6, 3), (-8, 4)):
        keep = (k >= low * sd - 1e-12) & (k <= high * sd + 1e-12)
        sums.append(quote_sum(exact_chain(PLAN_SLICE, 30, 100 * np.exp(k[keep])), 30.0).mid)
    assert sums == sorted(sums) and sums[0] < sums[-1]
    coarse = sd_strikes(PLAN_SLICE, -8, 4, 25)
    full = quote_sum(exact_chain(PLAN_SLICE, 30, coarse), 30.0).mid
    for i in range(1, 5):
        assert quote_sum(exact_chain(PLAN_SLICE, 30, coarse[i:]), 30.0).mid < full
        assert quote_sum(exact_chain(PLAN_SLICE, 30, coarse[:-i]), 30.0).mid < full


# ------------------------------------------------------ the quote sum's rules

def test_k0_is_the_highest_put_at_or_below_the_forward_and_parity_fills_its_call():
    strikes = [80.0, 85, 90, 95, 99, 101, 105, 110, 115, 120]
    plain = quote_sum(exact_chain(PLAN_SLICE, 30, strikes), 30.0)
    paired = quote_sum(exact_chain(PLAN_SLICE, 30, strikes, both_sides={99.0}), 30.0)
    assert plain.k0 == paired.k0 == 99.0
    assert plain.mid == pytest.approx(paired.mid, rel=1e-10)       # parity, or the quoted call
    assert list(plain.strikes) == strikes
    at_forward = exact_chain(PLAN_SLICE, 30, [*strikes, 100.0], rate=0.0, both_sides={100.0})
    assert at_forward.forward(30.0) == 100.0
    assert quote_sum(at_forward, 30.0).k0 == 100.0          # a strike at the forward is K0


def test_a_missing_bid_is_skipped_and_two_in_a_row_end_that_side():
    days, forward = 30.0, 100.0
    quotes = [OptionQuote("put", k, days, b, a) for k, b, a in (
        (60, 0.01, 0.02), (65, 0.0, 0.02), (70, 0.0, 0.02), (75, 0.03, 0.05),
        (80, 0.0, 0.05), (85, 0.10, 0.12), (90, 0.30, 0.32), (95, 0.90, 0.92))]
    quotes += [OptionQuote("call", k, days, b, a) for k, b, a in (
        (100, 1.60, 1.62), (105, 0.40, 0.42), (110, 0.0, 0.02), (115, 0.02, 0.03))]
    chain = OptionChain("X", "test", AS_OF, forward * math.exp(-0.04 * days / 365), 0.04, 0.0,
                        tuple(quotes))
    summed = quote_sum(chain, days)
    assert summed.k0 == 95
    assert list(summed.strikes) == [75, 85, 90, 95, 100, 105, 115]
    assert summed.no_bid == 4                       # 80, then 70 and 65; and 110
    assert summed.cut_off == 1                      # 60, past two missing bids in a row
    assert summed.bid < summed.mid < summed.ask
    lo, hi = summed.vol_band
    assert lo < summed.vol < hi


def test_quotes_that_cant_make_a_sum_give_none():
    above = exact_chain(PLAN_SLICE, 30, [101.0, 105, 110, 115])          # no put below F
    assert quote_sum(above, 30.0) is None
    thin = exact_chain(PLAN_SLICE, 30, [99.0, 101.0])                    # two strikes
    assert quote_sum(thin, 30.0) is None
    with pytest.raises(ValueError, match="no quotes at 31"):
        quote_sum(thin, 31.0)


# ------------------------------------------------------ 7. across expiries

def _expiry(dte: float, total: float, quoted_share: float = 0.9,
            sums: tuple[float, float, float] | None = None) -> ExpiryVariance:
    years = dte / 365
    smile = SmileVariance(years=years, total=total, quoted=quoted_share * total,
                          wings=(1 - quoted_share) * total, atm_total=0.8 * total)
    quotes = None if sums is None else QuoteSum(
        dte=dte, years=years, forward=100.0, discount=1.0, k0=100.0,
        strikes=np.array([95.0, 100.0, 105.0]), bid=sums[0], mid=sums[1], ask=sums[2],
        fit=None, no_bid=0, cut_off=0)
    return ExpiryVariance(dte=dte, smile=smile, quotes=quotes)


def test_interpolation_is_exact_on_additive_variance():
    # Forward variance 0.04 to day 20, 0.09 to day 50, 0.0625 to day 120.
    def total(days: float) -> float:
        legs = ((20, 0.04), (50, 0.09), (120, 0.0625))
        start, out = 0.0, 0.0
        for end, rate in legs:
            out += rate * max(0.0, min(days, end) - start) / 365
            start = end
        return out

    expiries = [_expiry(d, total(d), sums=(0.95 * total(d), total(d), 1.05 * total(d)))
                for d in (20, 50, 120)]
    for days in (20, 30, 45, 50, 77, 120):
        tenor = constant_maturity(expiries, days)
        assert tenor.smile.total == pytest.approx(total(days), rel=1e-12)
        assert tenor.quote_mid == pytest.approx(total(days), rel=1e-12)
        assert tenor.quote_bid == pytest.approx(0.95 * total(days), rel=1e-12)
        assert tenor.smile.extrapolated_share == pytest.approx(0.1)
    assert constant_maturity(expiries, 30).between == (20, 50)
    assert constant_maturity(expiries, 50).between == (50, 50)
    assert constant_maturity(expiries, 30).vol == pytest.approx(
        math.sqrt(total(30) / (30 / 365)))
    assert constant_maturity(expiries, 10) is None                 # never extrapolates
    assert constant_maturity(expiries, 121) is None
    forwards = forward_variances(expiries)
    assert [f.variance for f in forwards] == pytest.approx([0.09, 0.0625])


def test_quote_sums_interpolate_only_when_both_expiries_have_one():
    expiries = [_expiry(20, 0.002, sums=(0.0019, 0.002, 0.0021)), _expiry(50, 0.006)]
    tenor = constant_maturity(expiries, 30)
    assert tenor.quote_mid is None and tenor.quote_vol is None
    assert tenor.smile.total == pytest.approx(0.002 + (0.006 - 0.002) * 10 / 30)


def test_the_quote_sums_alone_at_a_constant_maturity():
    chain = synthetic_chain(SURFACE, SyntheticChainSpec(seed=2, k_range_sd=(-6, 3),
                                                        strikes_per_expiry=41))
    sums = {d: quote_sum(chain, d) for d in chain.expiries}
    at_30 = quote_tenor(chain, 30)
    assert at_30.between == (30.0, 30.0) and at_30.mid == sums[30.0].mid
    at_45 = quote_tenor(chain, 45)
    assert at_45.between == (30.0, 60.0)
    assert at_45.mid == pytest.approx(0.5 * sums[30.0].mid + 0.5 * sums[60.0].mid)
    assert at_45.bid < at_45.mid < at_45.ask
    assert at_45.vol == pytest.approx(math.sqrt(at_45.mid / (45 / 365)))
    low, high = at_45.vol_band
    assert low == pytest.approx(math.sqrt(at_45.bid / (45 / 365))) and low < at_45.vol < high
    assert quote_tenor(chain, 400) is None and quote_tenor(chain, 3) is None
    fitted = implied_variance(fit_surface(chain)).tenor(30)
    assert at_30.mid == pytest.approx(fitted.smile.total, rel=0.04)   # the wide band holds ~97%


def test_a_falling_total_variance_fails_the_calendar_check():
    expiries = [_expiry(20, 0.004), _expiry(50, 0.003), _expiry(90, 0.008)]
    forwards = forward_variances(expiries)
    assert [f.calendar_ok for f in forwards] == [False, True]
    assert forwards[0].variance == pytest.approx(-0.001 / (30 / 365))


def test_the_term_structure_of_a_fitted_chain():
    term = implied_variance(fit_surface(synthetic_chain(SURFACE, SyntheticChainSpec(seed=1))))
    headline = term.tenor()
    assert headline.days == HEADLINE_DAYS and headline.between == (30.0, 30.0)
    assert term.tenor(45).between == (30.0, 60.0)
    assert term.tenor(400) is None
    # The surface's variance mean-reverts down from 25% vol toward 20%, and so
    # do its forward variances, each near the true surface's.
    assert all(a.variance > b.variance for a, b in zip(term.forwards, term.forwards[1:]))
    true = forward_variances([
        ExpiryVariance(e.dte, smile_variance(SURFACE.slice(e.years), e.years, -0.1, 0.1), None)
        for e in term.expiries])
    for fitted, truth in zip(term.forwards, true):
        assert fitted.variance == pytest.approx(truth.variance, rel=0.02)


# ------------------------------------------------------- 8. inputs validated

def test_inputs_are_validated():
    with pytest.raises(ValueError, match="years"):
        smile_variance(PLAN_SLICE, 0.0, -0.1, 0.1)
    with pytest.raises(ValueError, match="k_min < k_max"):
        smile_variance(PLAN_SLICE, MONTH, 0.1, -0.1)
    with pytest.raises(ValueError, match="k_min < k_max"):
        smile_variance(PLAN_SLICE, MONTH, math.nan, 0.1)
    with pytest.raises(ValueError, match="low < high"):
        span_total(lambda k: k, 1.0, 1.0)
    with pytest.raises(ValueError, match="positive number of days"):
        constant_maturity([_expiry(20, 0.002)], 0)
    with pytest.raises(ValueError, match="straddle zero"):
        SyntheticChainSpec(k_range_sd=(0.5, 2.0))
    low, high = smile_bounds(PLAN_SLICE)
    assert low < 0 < high


def test_the_bounds_reach_eight_sd_in_z_on_both_sides():
    low, high = smile_bounds(STEEP_SLICE)
    for k, sign in ((low, -1), (high, 1)):
        root = math.sqrt(float(STEEP_SLICE.total_variance(k)))
        assert sign * (k / root + root / 2) >= 8.0
