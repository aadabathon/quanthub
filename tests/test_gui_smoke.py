"""Smoke tests of the assembled application, headless.

Runs on Qt's ``offscreen`` platform (no display, no window manager), against
a temporary store — never the user's database. It drives the real wiring:
the pump delivers TaskRunner results, the Dashboard renders a stored series
and its forecast, every nav entry builds, and the Ctrl+K commands work.
"""

import os
import time

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("qtawesome")

from quant_hub.app import create_app  # noqa: E402
from quant_hub.config import AppConfig  # noqa: E402
from quant_hub.storage.bar_store import BarStore, InstrumentKey  # noqa: E402


def wait_for(app, condition, timeout_s: float = 10.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while not condition():
        assert time.perf_counter() < deadline, "timed out waiting for the GUI"
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def qt_app(tmp_path):
    db = tmp_path / "market_data.db"
    seed_store = BarStore(db)
    dates = pd.bdate_range("2025-01-02", periods=300)
    closes = 50 * np.exp(np.cumsum(np.random.default_rng(2).normal(0, 0.012, len(dates))))
    frame = pd.DataFrame({"open": closes, "high": closes * 1.01, "low": closes * 0.99,
                          "close": closes, "volume": np.full(len(dates), 2e6)}, index=dates)
    for symbol in ("AAA", "BBB"):
        seed_store.insert_bars(InstrumentKey(symbol), "ibkr", "1 day", frame)
    seed_store.close()

    app, window = create_app(AppConfig(db_path=db, default_symbol="BBB"), argv=["test"])
    window.show()
    yield app, window
    app.aboutToQuit.emit()      # the same shutdown sequence a real quit runs
    window.close()
    window.deleteLater()
    # deleteLater only runs inside an event loop; flush it, or every test's
    # window would stay alive (and slow each later test down).
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_dashboard_renders_store_and_forecast(qt_app):
    app, window = qt_app
    dash = window.show_page("Dashboard")
    wait_for(app, lambda: dash._sandbox_stack.currentWidget() is dash._fan)
    assert dash._kpi_series._value.text() == "2"
    assert dash._kpi_bars._value.text() == "600"
    assert dash._symbol_box.current() == "BBB"          # config default honored
    assert dash._watchlist.rowCount() == 2
    assert dash._store_table.rowCount() == 2
    assert dash._explorer_stack.currentWidget() is dash._price_chart
    assert "BBB" in dash._sandbox.subtitle.full_text()


def test_window_and_symbol_changes_rerender(qt_app):
    app, window = qt_app
    dash = window.show_page("Dashboard")
    wait_for(app, lambda: dash._sandbox_stack.currentWidget() is dash._fan)
    dash._on_window_chosen("1M")
    wait_for(app, lambda: len(dash._price_chart._x) < 30)
    dash.select_symbol("AAA")
    wait_for(app, lambda: "AAA" in dash._sandbox.subtitle.full_text())
    assert dash._symbol_box.current() == "AAA"


def test_every_page_builds(qt_app):
    app, window = qt_app
    for name in list(window._factories):
        page = window.show_page(name)
        app.processEvents()
        assert window._stack.currentWidget() is page


def test_command_search_navigates(qt_app):
    app, window = qt_app
    dash = window.show_page("Dashboard")
    wait_for(app, lambda: any(k.startswith("Chart AAA")
                              for k in window._top.search._commands))
    commands = window._top.search._commands
    commands["Go to Volatility Lab"]()
    assert window._active == "Volatility Lab"
    next(action for name, action in commands.items() if name.startswith("Chart AAA"))()
    assert window._active == "Dashboard"
    wait_for(app, lambda: dash._symbol_box.current() == "AAA")


# ----------------------------------------------------------------- GARCH Lab

def test_garch_synthetic_fit_shows_truth(qt_app):
    app, window = qt_app
    lab = window.show_page("GARCH Lab")
    lab._days.set_text("400")
    lab._fit()
    wait_for(app, lambda: lab._stats.value_text("observations") == "400")
    assert lab._vol_stack.currentWidget() is lab._series_chart
    assert lab._stats._truth_header.isVisibleTo(lab)          # TRUE column shown
    names = [s.name for p in lab._series_chart._panels for s in p.series]
    assert names == ["Return", "True σ", "Fitted σ"]


def test_garch_market_history_on_synthetic_source(qt_app):
    app, window = qt_app
    lab = window.show_page("GARCH Lab")
    lab._modes.set_current("Market history")
    lab._on_mode("Market history")
    lab._source.select.set_current("synthetic")
    lab._symbol.set_text("SPY")
    lab._lookback.set_text("400")
    lab._fit()
    wait_for(app, lambda: "SPY" in lab._vol_card.subtitle.full_text())
    assert not lab._stats._truth_header.isVisibleTo(lab)      # no truth for real data
    assert not lab._groups["Synthetic"].isVisibleTo(lab)


def test_garch_invalid_input_is_flagged(qt_app):
    app, window = qt_app
    lab = window.show_page("GARCH Lab")
    lab._alpha.set_text("abc")
    lab._fit()
    assert lab._alpha.edit.property("invalid") is True
    assert "must be a number" in lab._header._status.full_text()


# ---------------------------------------------------------------- Kalman Lab

def _kalman_fit(app, lab, days="300"):
    lab._days.set_text(days)
    lab._fit()
    wait_for(app, lambda: lab._run_result is not None
             and len(lab._run_result.log_prices) == int(days))


def test_kalman_local_level_shows_truth_and_ewma(qt_app):
    app, window = qt_app
    lab = window.show_page("Kalman Lab")
    _kalman_fit(app, lab)
    table = lab._stats["Local level"]
    assert table.isVisibleTo(lab) and not lab._stats["Local trend"].isVisibleTo(lab)
    assert table._truth_header.isVisibleTo(lab)               # TRUE column shown
    assert table.value_text("observations") == "300"
    gain = lab._run_result.fit.parameters.steady_state_gain
    assert table.value_text("gain") == f"{gain:.4f}"
    names = [s.name for p in lab._series_chart._panels for s in p.series]
    assert names == ["Close", "True level", "Smoothed (hindsight)", "Filtered level"]
    assert [b.name for b in lab._series_chart._panels[0].bands] == ["95% band"]
    # The chart shows a trailing window; the fit used every close.
    assert len(lab._series_chart._x) == 252
    lab._on_window("ALL")
    assert len(lab._series_chart._x) == 300


def test_kalman_trend_adds_drift_panel(qt_app):
    app, window = qt_app
    lab = window.show_page("Kalman Lab")
    lab._models.set_current("Local trend")
    lab._on_model("Local trend")
    assert lab._slope_sd.isVisibleTo(lab)
    _kalman_fit(app, lab, days="400")
    panels = lab._series_chart._panels
    assert [p.title.split(" ·")[0] for p in panels] == ["Price", "Slope"]
    assert panels[1].y_range is not None
    assert lab._stats["Local trend"].isVisibleTo(lab)
    assert lab._stats["Local trend"].value_text("drift").endswith("%")


def test_kalman_market_history_on_synthetic_source(qt_app):
    app, window = qt_app
    lab = window.show_page("Kalman Lab")
    wait_for(app, lambda: lab._run_result is not None)        # the first-show fit
    lab._modes.set_current("Market history")
    lab._on_mode("Market history")
    lab._source.select.set_current("synthetic")
    lab._symbol.set_text("SPY")
    lab._lookback.set_text("400")
    lab._fit()
    wait_for(app, lambda: "SPY" in lab._level_card.subtitle.full_text())
    assert not lab._stats["Local level"]._truth_header.isVisibleTo(lab)
    names = [s.name for p in lab._series_chart._panels for s in p.series]
    assert "True level" not in names


def test_kalman_zero_noise_note(qt_app):
    from quant_hub.gui.pages.kalman_lab import _Run
    from quant_hub.models.kalman import fit_local_level, rts_smoother

    app, window = qt_app
    lab = window.show_page("Kalman Lab")
    wait_for(app, lambda: lab._run_result is not None)
    assert not lab._zero_note.isVisibleTo(lab)
    # Positively autocorrelated changes: the fit puts the noise at zero.
    shocks = np.random.default_rng(9).normal(scale=0.01, size=400)
    changes = np.empty_like(shocks)
    changes[0] = shocks[0]
    for t in range(1, len(shocks)):
        changes[t] = 0.3 * changes[t - 1] + shocks[t]
    y = 4.6 + np.cumsum(changes)
    fit = fit_local_level(y)
    assert fit.zero_variances == ("noise_variance",)
    lab._on_done(_Run(pd.bdate_range("2024-01-02", periods=len(y)), y, fit,
                      rts_smoother(fit.filtered), None, None, None, "AR changes"))
    assert lab._zero_note.isVisibleTo(lab)
    assert "Noise σ fit to zero" in lab._zero_note._text.text()
    assert lab._stats["Local level"].value_text("noise_sd") == "≈ 0"
    assert lab._stats["Local level"].value_text("q") == "∞"


def test_kalman_invalid_noise_is_flagged(qt_app):
    app, window = qt_app
    lab = window.show_page("Kalman Lab")
    lab._noise_sd.set_text("0")
    lab._fit()
    assert lab._noise_sd.edit.property("invalid") is True
    assert "must be positive" in lab._header._status.full_text()


# ------------------------------------------------------------------- Markets

def test_markets_synthetic_quotes_add_and_remove(qt_app):
    app, window = qt_app
    markets = window.show_page("Markets")
    markets._source.set_current("synthetic")
    wait_for(app, lambda: all(o is not None for o in markets._outcomes.values()))
    assert markets._table.rowCount() == len(markets._symbols)
    markets._add_edit.setText("ZZZ")
    markets._add_symbol()
    wait_for(app, lambda: markets._outcomes.get("ZZZ") is not None)
    assert markets._selected == "ZZZ"
    assert markets._d_symbol.text() == "ZZZ" and markets._d_price.text() != "—"
    markets._remove_selected()
    assert "ZZZ" not in markets._symbols
    assert markets._table.rowCount() == len(markets._symbols)


def test_markets_unavailable_reason_and_stored_history(qt_app):
    app, window = qt_app
    markets = window.show_page("Markets")
    markets._source.set_current("ibkr")                       # not connected in tests
    markets._add_edit.setText("AAA")                          # stored in the temp db
    markets._add_symbol()
    wait_for(app, lambda: isinstance(markets._outcomes.get("AAA"), Exception)
             and "AAA" in markets._stored)
    assert markets._d_badge.text() == "UNAVAILABLE"
    assert markets._d_note.isVisibleTo(markets)
    assert "300 daily bars" in markets._d_stored.text()
    assert markets._chart_button.isVisibleTo(markets)


# -------------------------------------------------------------------- charts

def test_stacked_chart_hover_moves_readout(qt_app):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    app, window = qt_app
    lab = window.show_page("GARCH Lab")
    lab._days.set_text("300")
    lab._fit()
    wait_for(app, lambda: lab._stats.value_text("observations") == "300")
    chart = lab._series_chart
    before = chart._readout.text()
    viewport = chart._canvas.viewport()
    point = QPointF(viewport.width() * 0.3, viewport.height() * 0.7)
    QApplication.sendEvent(viewport, QMouseEvent(
        QEvent.MouseMove, point, QPointF(viewport.mapToGlobal(point.toPoint())),
        Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    assert chart._readout.text() != before
    assert all(line.isVisible() for line in chart._vlines)


# ----------------------------------------------------------------- Simulator

def _hover(widget, fx: float, fy: float) -> None:
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    viewport = widget.viewport()
    point = QPointF(viewport.width() * fx, viewport.height() * fy)
    QApplication.sendEvent(viewport, QMouseEvent(
        QEvent.MouseMove, point, QPointF(viewport.mapToGlobal(point.toPoint())),
        Qt.NoButton, Qt.NoButton, Qt.NoModifier))


def _run_small_simulation(app, sim_page, days="60", paths="200"):
    sim_page._days.set_text(days)
    sim_page._paths.set_text(paths)
    sim_page._run()
    wait_for(app, lambda: sim_page._sim is not None and sim_page._sim.n_days == int(days)
             and sim_page._sim.n_paths == int(paths))


def test_simulator_run_renders_one_consistent_state(qt_app):
    app, window = qt_app
    page = window.show_page("Simulator")
    _run_small_simulation(app, page)
    sim, analysis = page._sim, page._analysis
    assert analysis.history is sim.history                     # D15: same draw everywhere
    assert page._stats.value_text("n_observations") == "61"
    assert page._stats._truth_header.isVisibleTo(page)
    assert page._path_stack.currentWidget() in (page._series_chart, page._fan)
    assert "200 paths" in page._path_card.subtitle.full_text()


def test_simulator_reanalyze_keeps_the_draw(qt_app):
    app, window = qt_app
    page = window.show_page("Simulator")
    _run_small_simulation(app, page)
    sim = page._sim
    page._window.set_text("10")
    page._reanalyze()
    wait_for(app, lambda: page._analysis.vol_window == 10)
    assert page._sim is sim                                    # no re-simulation
    assert "10-day window" in page._header._status.full_text()


def test_simulator_views_and_return_kind(qt_app):
    app, window = qt_app
    page = window.show_page("Simulator")
    _run_small_simulation(app, page)
    page._views.set_current("Monte Carlo")
    page._on_view("Monte Carlo")
    assert page._path_stack.currentWidget() is page._fan
    assert not page._window.isVisibleTo(page)                  # vol window only for the path
    _hover(page._fan._canvas, 0.7, 0.5)
    assert "Sample path" in page._fan._readout.text()
    page._on_returns_kind("Simple")
    assert "simple returns" in page._returns_card.subtitle.full_text()


def test_simulator_invalid_paths_flagged(qt_app):
    app, window = qt_app
    page = window.show_page("Simulator")
    page._paths.set_text("1")
    page._run()
    assert page._paths.edit.property("invalid") is True
    assert "at least 2" in page._header._status.full_text()


# --------------------------------------------------------------- Options Lab

def _manual_lab(app, window, spot="100", vol="20", rate="4"):
    lab = window.show_page("Options Lab")
    u = lab._underlying
    u._source.select.set_current("manual")
    u._spot.set_text(spot)
    u._vol.set_text(vol)
    u._rate.set_text(rate)
    u._edited()
    app.processEvents()
    return lab


def test_options_pricer_prices_and_inverts(qt_app):
    app, window = qt_app
    lab = _manual_lab(app, window)
    pricer = lab._pricer
    pricer._strike.set_text("100")
    pricer._dte.set_text("30")
    pricer._on_mode("Price from σ")
    model_price = float(pricer._hero.text())
    assert 2.0 < model_price < 3.5                      # ATM 30d call, σ 20%, r 4%
    assert pricer._tiles["delta"].value_text().startswith("+0.5")
    # Invert the model's own price: the solver must recover σ = 20%.
    pricer._quote.set_text(f"{model_price:.6f}")
    pricer._on_mode("σ from price")
    assert pricer._hero.text() == "20.00%"


def test_options_pricer_live_update_and_error_card(qt_app):
    app, window = qt_app
    lab = _manual_lab(app, window)
    pricer = lab._pricer
    pricer._on_mode("Price from σ")
    pricer._strike.edit.setText("110")
    pricer._strike.edit.textEdited.emit("110")         # typing, not Enter
    wait_for(app, lambda: "K=110" in pricer._chart_card.subtitle.full_text())
    pricer._quote.set_text("150")                       # above the call's bound S·e^(−qT)
    pricer._on_mode("σ from price")
    assert pricer._chart_stack.currentWidget() is pricer._error
    assert lab._header._status.property("role") == "statusError"


def test_options_strategy_template_slider_and_legs(qt_app):
    app, window = qt_app
    lab = _manual_lab(app, window)
    lab._tabs.set_current("Strategy")
    lab._on_tab("Strategy")
    strategy = lab._strategy
    strategy._template.select.set_current("Iron condor")
    assert len(strategy._legs) == 4 and strategy._table.rowCount() == 4
    assert strategy._tiles["max_loss"].value_text().startswith("-")
    strategy._slider.setValue(10)
    assert strategy._horizon_label.text() == "T + 10d"
    assert len(strategy._chart._curves) == 2            # expiry + T+10d
    strategy._new_strike.set_text("120")
    strategy._add_leg()
    assert len(strategy._legs) == 5
    strategy._table.selectRow(4)
    strategy._remove_leg()
    assert len(strategy._legs) == 4


def test_options_monte_carlo_runs_and_goes_stale(qt_app):
    app, window = qt_app
    lab = _manual_lab(app, window)
    lab._pricer._on_mode("Price from σ")
    mc = lab._monte_carlo
    mc._paths = "10K"
    mc.run()
    wait_for(app, lambda: mc._result is not None)
    assert mc._badge.text() in ("CONSISTENT", "OUTSIDE 95%")
    assert mc._stack.currentWidget() is mc._chart
    lab._underlying._spot.set_text("101")
    lab._underlying._edited()
    assert mc._badge.text() == "STALE"


def test_options_load_recentres_and_flags_edits(qt_app):
    app, window = qt_app
    lab = window.show_page("Options Lab")
    u = lab._underlying
    u._symbol.set_text("SPY")
    u._source.select.set_current("synthetic")          # triggers a load
    wait_for(app, lambda: u._snapshot is not None)
    spot = u._snapshot.spot
    assert u._badge.text() == "LOADED"
    assert abs(float(lab._pricer._strike.text()) - spot) <= spot * 0.05   # re-centred
    u._spot.set_text(f"{spot + 1:.2f}")
    u._edited()
    assert u._badge.text() == "EDITED"


def test_options_state_round_trip_keeps_saved_shape(qt_app):
    """The saved shape predates the Qt UI; keeping it keeps users' inputs."""
    app, window = qt_app
    lab = _manual_lab(app, window)
    lab.on_hide()
    saved = lab._state_store.load("options_lab")
    assert set(saved) == {"market", "pricer", "strategy", "monte_carlo", "tab"}
    assert saved["pricer"]["mode"] in ("price from vol", "vol from price")
    assert saved["monte_carlo"]["paths"] in ("10,000", "100,000", "1,000,000")


# ------------------------------------------------------------ Volatility Lab

def _fitted_vol_lab(app, window):
    lab = window.show_page("Volatility Lab")              # fits the defaults on first show
    wait_for(app, lambda: lab._fit is not None)
    return lab


def test_vol_lab_fit_fills_every_view_and_diagnostics(qt_app):
    app, window = qt_app
    lab = _fitted_vol_lab(app, window)
    assert lab._table.rowCount() == len(lab._fit.slices) == 6
    assert lab._checks.value_text("butterfly").startswith("ok")
    assert lab._checks.value_text("calendar").startswith("ok")
    assert len(lab._smiles._panels) == 6
    assert lab._surface._values.shape[0] == 6
    for view, widget in (("Term structure", lab._term), ("Surface", lab._surface),
                         ("Smiles", lab._smiles)):
        lab._on_view(view)
        assert lab._stack.currentWidget() is widget
    _hover(lab._surface._canvas, 0.4, 0.5)
    assert "IV" in lab._surface._readout.text() and "k/√θ" in lab._surface._readout.text()


def test_vol_lab_rejects_bad_expiries(qt_app):
    app, window = qt_app
    lab = _fitted_vol_lab(app, window)
    lab._fields["expiries"].set_text("7, thirty")
    lab._run()
    assert lab._fields["expiries"].edit.property("invalid") is True
    assert "comma-separated" in lab._header._status.full_text()


def test_vol_lab_history_overlay(qt_app):
    app, window = qt_app
    lab = _fitted_vol_lab(app, window)
    lab._source.set_current("synthetic")
    lab._symbol.set_text("SPY")
    lab._lookback.set_text("800")
    lab._load_history()
    wait_for(app, lambda: lab._context is not None)
    names = [c.name for c in lab._term_chart._curves]
    assert any(name.startswith("GARCH forecast") for name in names)
    assert "GARCH" in lab._context_label.text()


def test_vol_lab_heavy_noise_still_renders(qt_app):
    app, window = qt_app
    lab = _fitted_vol_lab(app, window)
    lab._fields["spread"].set_text("40")
    lab._fields["noise"].set_text("2")
    first = lab._fit
    lab._run()
    wait_for(app, lambda: lab._fit is not first)
    assert lab._header._status.property("role") == "statusInfo"
    assert lab._table.rowCount() == len(lab._fit.slices)
