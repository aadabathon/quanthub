"""Monte Carlo pricing: statistical agreement with the closed form, the
antithetic variance-reduction guarantee, and the running diagnostics."""

import math

import numpy as np
import pytest

from quant_hub.data.synthetic import GeometricBrownianMotion
from quant_hub.models.black_scholes import BlackScholesInputs
from quant_hub.models.option_monte_carlo import monte_carlo_price, risk_neutral_terminal_prices


def inputs(**overrides):
    base = dict(spot=100.0, strike=105.0, dte=90.0, rate=0.04, sigma=0.3, option_type="call",
                dividend_yield=0.02)
    base.update(overrides)
    return BlackScholesInputs(**base)


@pytest.mark.parametrize("antithetic", [True, False])
@pytest.mark.parametrize(
    "case",
    [{}, {"option_type": "put"}, {"strike": 80.0, "dte": 365.0}, {"strike": 130.0, "sigma": 0.6}],
)
def test_agrees_with_closed_form(case, antithetic):
    # |z| < 4 fails by chance with probability ~6e-5 per case; the seed is
    # fixed so the suite is deterministic anyway.
    result = monte_carlo_price(inputs(**case), 200_000, seed=11, antithetic=antithetic)
    assert abs(result.z_score) < 4.0
    assert result.estimate == pytest.approx(result.closed_form, rel=0.02)


def test_terminal_mean_is_the_forward():
    # E[S_T] = S e^{(r-q)T} under the risk-neutral measure.
    x = inputs()
    result = monte_carlo_price(x, 400_000, seed=3, antithetic=False)
    t = x.dte / 365.0
    forward = x.spot * math.exp((x.rate - x.dividend_yield) * t)
    se = result.terminal_prices.std(ddof=1) / math.sqrt(len(result.terminal_prices))
    assert abs(result.terminal_prices.mean() - forward) < 4 * se


def test_antithetic_reduces_standard_error_at_equal_cost():
    plain = monte_carlo_price(inputs(), 100_000, seed=5, antithetic=False)
    anti = monte_carlo_price(inputs(), 100_000, seed=5, antithetic=True)
    assert anti.standard_error < plain.standard_error


def test_plain_sampling_is_the_synthetic_gbm_transition():
    # Same exact law and draw order as data/synthetic.py with mu = r - q.
    x = inputs()
    result = monte_carlo_price(x, 1000, seed=21, antithetic=False)
    gbm = GeometricBrownianMotion(
        initial_price=x.spot, mu=x.rate - x.dividend_yield, sigma=x.sigma,
        dt=x.dte / 365.0, seed=21,
    )
    assert result.terminal_prices == pytest.approx(gbm.simulate_paths(1, 1000)[:, 1], rel=1e-12)


def test_running_diagnostics_are_prefix_statistics():
    result = monte_carlo_price(inputs(), 20_000, seed=8, antithetic=False)
    assert np.all(np.diff(result.checkpoints) > 0)
    assert result.checkpoints[-1] == 20_000
    assert result.running_estimate[-1] == result.estimate
    k = int(result.checkpoints[len(result.checkpoints) // 2])
    t = inputs().dte / 365.0
    payoffs = math.exp(-inputs().rate * t) * np.maximum(result.terminal_prices[:k] - 105.0, 0.0)
    i = len(result.checkpoints) // 2
    assert result.running_estimate[i] == pytest.approx(payoffs.mean())
    assert result.running_standard_error[i] == pytest.approx(payoffs.std(ddof=1) / math.sqrt(k))


def test_antithetic_checkpoints_count_paths():
    result = monte_carlo_price(inputs(), 10_000, seed=1, antithetic=True)
    assert result.checkpoints[-1] == 10_000
    assert np.all(result.checkpoints % 2 == 0)


def test_seeded_runs_are_deterministic():
    a = monte_carlo_price(inputs(), 5000, seed=4)
    b = monte_carlo_price(inputs(), 5000, seed=4)
    assert a.estimate == b.estimate and a.standard_error == b.standard_error


def test_all_out_of_the_money_gives_nan_z_score():
    result = monte_carlo_price(inputs(strike=10_000.0, dte=1.0), 1000, seed=1)
    assert result.estimate == 0.0 and math.isnan(result.z_score)


def test_terminal_mapping_is_monotone():
    z = np.linspace(-3, 3, 7)
    assert np.all(np.diff(risk_neutral_terminal_prices(inputs(), z)) > 0)


@pytest.mark.parametrize("n,antithetic", [(3, False), (1001, True)])
def test_invalid_path_counts_rejected(n, antithetic):
    with pytest.raises(ValueError):
        monte_carlo_price(inputs(), n, antithetic=antithetic)
