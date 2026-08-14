"""Rendering smoke tests: every chart function draws without error into a
backend-free ``matplotlib.figure.Figure`` (no GUI, no display needed).

These do not assert pixels — they assert the rendering contract: functions
accept the analytics objects, clear and repopulate the figure in place, and
tolerate edge cases (short series, single path) without raising.
"""

import pytest

from quant_hub.data.simulation import monte_carlo_summary, run_gbm_simulation
from quant_hub.data.stats import analyze_history
from quant_hub.visualization.charts import (
    ChartStyle,
    create_figure,
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
