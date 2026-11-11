"""Rendering smoke tests: every chart function draws without error into a
backend-free ``matplotlib.figure.Figure`` (no GUI, no display needed).

These do not assert pixels — they assert the rendering contract: functions
accept the analytics objects, clear and repopulate the figure in place, and
tolerate edge cases (short series, single path) without raising.
"""

import pytest

from quant_hub.data.simulation import monte_carlo_summary, run_gbm_simulation
from quant_hub.data.stats import analyze_history
from quant_hub.models.garch import GARCH11Parameters, fit_garch11, simulate_garch11
from quant_hub.visualization.charts import (
    ChartStyle,
    create_figure,
    plot_garch_overview,
    plot_lab_overview,
    plot_message,
    plot_monte_carlo,
    plot_price_history,
)


@pytest.fixture(scope="module")
def sim():
    return run_gbm_simulation(
        initial_price=100.0, mu=0.08, sigma=0.20, n_days=252, seed=42, n_paths=50
    )


@pytest.fixture(scope="module")
def analysis(sim):
    return analyze_history(sim.history)


def test_create_figure_is_themed():
    style = ChartStyle()
    fig = create_figure(style)
    assert fig.patch.get_facecolor() != (1.0, 1.0, 1.0, 1.0)


def test_plot_price_history(sim):
    fig = create_figure()
    plot_price_history(fig, sim.history)
    assert len(fig.axes) == 1
    fig.canvas.draw()  # force a full render pass


def test_plot_lab_overview_draws_four_panels(sim, analysis):
    fig = create_figure()
    plot_lab_overview(fig, analysis, return_kind="log", true_sigma=sim.params.sigma)
    assert len(fig.axes) == 4
    fig.canvas.draw()


def test_plot_lab_overview_simple_returns_without_truth(analysis):
    fig = create_figure()
    plot_lab_overview(fig, analysis, return_kind="simple", true_sigma=None)
    assert len(fig.axes) == 4
    fig.canvas.draw()


def test_plot_lab_overview_rejects_unknown_return_kind(analysis):
    with pytest.raises(ValueError):
        plot_lab_overview(create_figure(), analysis, return_kind="excess")


def test_plot_lab_overview_short_series():
    # 5 prices: rolling-vol window exceeds the sample; must render regardless.
    short = run_gbm_simulation(
        initial_price=50.0, mu=0.05, sigma=0.30, n_days=4, seed=1
    )
    fig = create_figure()
    plot_lab_overview(fig, analyze_history(short.history))
    fig.canvas.draw()


def test_plot_monte_carlo(sim):
    fig = create_figure()
    plot_monte_carlo(fig, sim, monte_carlo_summary(sim))
    assert len(fig.axes) == 2
    fig.canvas.draw()


def test_plot_monte_carlo_single_path():
    single = run_gbm_simulation(
        initial_price=100.0, mu=0.08, sigma=0.20, n_days=30, seed=2, n_paths=1
    )
    fig = create_figure()
    plot_monte_carlo(fig, single, monte_carlo_summary(single))
    fig.canvas.draw()


def test_plot_redraws_in_place(sim, analysis):
    # The GUI owns one figure per canvas; successive plots must not accumulate axes.
    fig = create_figure()
    plot_lab_overview(fig, analysis)
    plot_monte_carlo(fig, sim, monte_carlo_summary(sim))
    assert len(fig.axes) == 2
    plot_message(fig, "no simulation yet")
    assert len(fig.axes) == 0

def test_plot_garch_overview():
    simulation = simulate_garch11(
        GARCH11Parameters(mean=0.0002, omega=0.000004, alpha=0.08, beta=0.88),
        600,
        seed=8,
    )
    import pandas as pd
    returns = pd.Series(
        simulation.returns, index=pd.bdate_range("2024-01-02", periods=600)
    )
    fig = create_figure()
    plot_garch_overview(fig, returns, fit_garch11(simulation.returns))
    assert len(fig.axes) == 3
    fig.canvas.draw()


def test_plot_option_profile():
    import numpy as np

    from quant_hub.models.black_scholes import price_array
    from quant_hub.visualization.charts import plot_option_profile

    spots = np.linspace(70.0, 130.0, 121)
    curves = [(f"{d}d", price_array(spots, 100.0, d, 0.05, 0.2, "call")) for d in (30, 15, 7, 1)]
    fig = create_figure()
    plot_option_profile(fig, spots, curves, "price", 100.0, 100.0)
    assert len(fig.axes) == 1 and len(fig.axes[0].get_legend().get_texts()) == 4
    fig.canvas.draw()


def test_plot_strategy_pnl_with_and_without_horizon():
    from quant_hub.models.black_scholes import MarketInputs
    from quant_hub.models.strategy import STRATEGY_TEMPLATES, expiry_profile, spot_grid, strategy_pnl
    from quant_hub.visualization.charts import plot_strategy_pnl

    market = MarketInputs(100.0, 0.05, 0.25)
    strategy = STRATEGY_TEMPLATES["Iron condor"](100.0, 30.0, 0.05)
    spots = spot_grid(strategy, market)
    expiry = strategy_pnl(strategy, market, spots, 30.0)
    fig = create_figure()
    plot_strategy_pnl(fig, spots, expiry, "at expiry", 100.0, expiry_profile(strategy, market).breakevens,
                      strategy_pnl(strategy, market, spots, 0.0), "T+0d")
    fig.canvas.draw()
    plot_strategy_pnl(fig, spots, expiry, "at expiry", 100.0, ())
    assert len(fig.axes) == 1


def test_plot_monte_carlo_pricing():
    from quant_hub.models.black_scholes import BlackScholesInputs
    from quant_hub.models.option_monte_carlo import monte_carlo_price
    from quant_hub.visualization.charts import plot_monte_carlo_pricing

    result = monte_carlo_price(BlackScholesInputs(100.0, 100.0, 30.0, 0.05, 0.2, "put"), 2000, seed=1)
    fig = create_figure()
    plot_monte_carlo_pricing(fig, result)
    assert len(fig.axes) == 2
    fig.canvas.draw()


def test_volatility_lab_charts():
    import numpy as np

    from quant_hub.models.svi import SSVISurface
    from quant_hub.models.vol_surface import (
        SyntheticChainSpec, fit_surface, smile_curves, surface_grid, synthetic_chain,
    )
    from quant_hub.visualization.charts import plot_smiles, plot_vol_heatmap, plot_vol_term_structure

    truth = SSVISurface(v0=0.0625, v_inf=0.04, kappa=2.0, rho=-0.6, eta=1.2)
    fit = fit_surface(synthetic_chain(truth, SyntheticChainSpec(seed=3)))
    fig = create_figure()
    plot_smiles(fig, smile_curves(fit, truth))
    assert len(fig.axes) == 6
    fig.canvas.draw()

    days = np.array([s.dte for s in fit.slices])
    grid = np.linspace(1, 365, 50)
    plot_vol_term_structure(fig, days, np.array([s.atm_vol for s in fit.slices]), grid,
                            np.array([truth.atm_vol(d / 365) for d in grid]), grid * 0 + 0.2,
                            "GARCH", [(30.0, 0.21), (365.0, 0.19)])
    fig.canvas.draw()
    plot_vol_term_structure(fig, days, np.array([s.atm_vol for s in fit.slices]))

    plot_vol_heatmap(fig, *surface_grid(fit))
    assert len(fig.axes) == 2  # heatmap + colorbar
    fig.canvas.draw()
