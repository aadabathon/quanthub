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

    app, window = create_app(AppConfig(db_path=db, default_symbol="BBB",
                                       regime_path=tmp_path / "REGIME.md"), argv=["test"])
    window.show()
    yield app, window
    _close(app, window)


def _close(app, window) -> None:
    app.aboutToQuit.emit()      # the same shutdown sequence a real quit runs
    window.close()
    window.deleteLater()
    # deleteLater only runs inside an event loop; flush it, or every test's
    # window would stay alive (and slow each later test down).
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_the_store_is_versioned_and_backed_up_before_the_stores_open(qt_app, tmp_path):
    # The fixture's store predates versioning, so startup copied it first (F1).
    backups = list((tmp_path / "backups").iterdir())
    assert len(backups) == 1 and backups[0].name.startswith("market_data.v0.")


def test_ibkr_turned_off_leaves_no_controls_or_sources(tmp_path):
    # D34: someone who doesn't use IBKR sets enabled = false in settings.toml.
    app, window = create_app(AppConfig(db_path=tmp_path / "s.db", ibkr_enabled=False,
                                       regime_path=tmp_path / "REGIME.md"),
                             argv=["test"])
    try:
        window.show()
        core = window._core
        assert core.market_service.source_names == ["synthetic", "file"]
        assert core.chains.source_names == ["stored"]
        assert not window._top._button.isVisibleTo(window)
        assert not window.status._ibkr.isVisibleTo(window)
        lab = window.show_page("Kalman Lab")
        assert lab._source.current() == "synthetic"     # its saved default was ibkr
        assert core.accounts is None
        assert not hasattr(window.show_page("Portfolio"), "_broker_button")   # E1
    finally:
        _close(app, window)


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


def test_dashboard_imports_a_csv_beside_the_same_symbol_from_ibkr(qt_app, tmp_path):
    # F2: the fixture stores AAA and BBB from ibkr; a file of AAA sits beside
    # them under "file", and the explorer charts the import.
    app, window = qt_app
    dash = window.show_page("Dashboard")
    wait_for(app, lambda: dash._store_table.rowCount() == 2)
    dates = pd.bdate_range("2024-01-02", periods=60)
    closes = 20 * np.exp(np.cumsum(np.random.default_rng(5).normal(0, 0.01, len(dates))))
    path = tmp_path / "aaa.csv"
    pd.DataFrame({"Date": dates.strftime("%Y-%m-%d"), "Close": closes}).to_csv(path, index=False)
    dash.import_files([path])
    wait_for(app, lambda: "Imported AAA" in dash._header._status.full_text())
    wait_for(app, lambda: dash._store_table.rowCount() == 3)
    assert "60 bars" in dash._header._status.full_text()
    wait_for(app, lambda: dash._symbol_box.current() == "AAA · file")
    from quant_hub.data.types import PriceBasis
    history = window._core.market_service.get_history(
        "AAA", dates[0].to_pydatetime(), dates[-1].to_pydatetime(), source="file",
        basis=PriceBasis.TRADED)
    assert len(history.closes) == 60


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


def test_research_runs_saves_reopens_and_retains_the_whole_grid(qt_app):
    app, window = qt_app
    page = window.show_page("Research")
    for key, value in {"train_size": "60", "lookback": "10", "observations": "180"}.items():
        page._fields[key].set_text(value)
    page._run()
    wait_for(app, lambda: page._run_result is not None)
    original = page._run_result
    assert original.payload["summary"]["observations"] == 119
    assert page._chart_stack.currentWidget() is page._chart
    assert page._fits.rowCount() > 0 and page._trades.rowCount() > 0
    page._notes.setPlainText("Reject pending an untouched holdout")
    page._save_conclusion()
    wait_for(app, lambda: page._header._status.full_text() == "Research notes saved")
    page._tab("Saved runs")
    wait_for(app, lambda: len(page._history._shown) == 1)
    page.open_run(original.id)
    wait_for(app, lambda: not page._busy)
    assert page._notes.toPlainText().startswith("Reject")
    assert page._stack.currentIndex() == 0
    page._grid_lookbacks.set_text("5, 10")
    page._grid_thresholds.set_text("0, 0.02")
    page._independent.set_text("3")
    page._sweep()
    wait_for(app, lambda: not page._busy)
    assert "sweep" in page._run_result.payload
    assert page._detail.currentIndex() == 2
    assert "not an untouched" in page._selection.text()
    assert len(window._core.research.list_runs()) == 6


def test_research_library_imports_notes_and_links_to_experiments(qt_app, tmp_path):
    app, window = qt_app
    page = window.show_page("Research")
    for key, value in {"train_size": "60", "lookback": "10", "observations": "180"}.items():
        page._fields[key].set_text(value)
    page._run()
    wait_for(app, lambda: page._run_result is not None)
    page._tab("Papers")
    library = page._library
    wait_for(app, lambda: len(library._papers) == 5)
    library._search.set_text("Fama")
    assert len(library._shown) == 1
    library._select(0)
    library._notes.setPlainText("Replicate the factor construction")
    library._link()
    wait_for(app, lambda: not library._busy)
    assert library._selected["run_ids"] == [page._run_result.id]
    assert library._selected["notes"] == "Replicate the factor construction"
    library._open_link()
    wait_for(app, lambda: not page._busy)
    assert page._stack.currentIndex() == 0
    path = tmp_path / "Research fixture.pdf"
    path.write_bytes(b"%PDF-1.4\n% fixture")
    library.import_paths([str(path)])
    wait_for(app, lambda: not library._busy)
    wait_for(app, lambda: len(library._papers) == 6)
    assert any(p["title"] == "Research fixture" and p["pdf"] for p in library._papers)


def test_startup_and_first_visits_do_not_show_temporary_windows(tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QApplication, QWidget
    from quant_hub.gui.pages.volatility_lab import VolatilityLabPage

    # Observe Show events, not just allWindows() after construction: the
    # unwanted subtitle windows lasted only a few milliseconds.
    shown = []

    class WindowShows(QObject):
        def eventFilter(self, widget, event):
            if event.type() == QEvent.Show and isinstance(widget, QWidget) and widget.isWindow():
                shown.append(type(widget).__name__)
            return False

    runs = []
    run = VolatilityLabPage._run

    def record_run(page):
        runs.append(True)
        run(page)

    monkeypatch.setattr(VolatilityLabPage, "_run", record_run)
    app = QApplication.instance() or QApplication(["test"])
    observer = WindowShows()
    app.installEventFilter(observer)
    window = None
    try:
        app, window = create_app(AppConfig(db_path=tmp_path / "startup.db", ibkr_enabled=False,
                                       regime_path=tmp_path / "REGIME.md"))
        window.show()
        app.processEvents()
        assert window._active == "Dashboard"
        assert runs == []     # preparing the browser must not start acquisition or a fit
        native_id = window.winId()
        # Vol first, so its fit finishes before the other labs queue work.
        lab = window.show_page("Volatility Lab")
        wait_for(app, lambda: lab._fit is not None)
        assert runs == [True]
        for name in window._factories:
            window.show_page(name)
            app.processEvents()
            assert window.winId() == native_id
        assert shown == ["MainWindow"]
    finally:
        app.removeEventFilter(observer)
        if window is not None:
            _close(app, window)


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


# ---------------------------------------------------------------- Markov Lab
def _markov_lab(app, window, **state):
    """The Markov Lab with small inputs saved before its first fit (fits are
    the slow part: the default 2,520-day BIC sweep takes ~4 s)."""
    window._core.state_store.save("markov_lab", {
        "days": "400", "count": "2 regimes", "world": "Two regimes", **state})
    lab = window.show_page("Markov Lab")
    wait_for(app, lambda: lab._run_result is not None, timeout_s=30)
    return lab


def test_markov_lab_synthetic_fit_shows_truth_and_views(qt_app):
    app, window = qt_app
    lab = _markov_lab(app, window)
    run = lab._run_result
    assert run.fit.model.n_regimes == 2 and len(run.returns) == 400
    table = lab._stats[2]
    assert table.isVisibleTo(lab) and table._truth_header.isVisibleTo(lab)
    assert table.value_text("vol1").endswith("%")
    assert table.value_text("bic1") == "not fitted"           # one count, no sweep
    panels = lab._chart._panels
    assert [p.title.split(" ·")[0] for p in panels] == [
        "Price", "Filtered probabilities", "True regime"]
    assert panels[1].stacked and len(panels[0].shades) == 1   # calm isn't tinted
    np.testing.assert_allclose(panels[1].series[1].values, run.filtered.filtered[:, 1])
    # The hindsight views swap the probabilities, never the fit.
    lab._on_view("Smoothed")
    np.testing.assert_allclose(lab._chart._panels[1].series[1].values, run.smoothed[:, 1])
    lab._on_view("Viterbi")
    assert set(np.unique(lab._chart._panels[1].series[0].values)) <= {0.0, 1.0}
    assert lab._run_result is run
    assert lab._transitions.rowCount() == 2 and "(" in lab._transitions.item(0, 1).text()
    assert lab._odds.rowCount() == 2
    # The chart shows a trailing window; the fit used every return.
    lab._on_window("1Y")
    assert len(lab._chart._x) == 252


def test_markov_lab_auto_on_market_history(qt_app):
    # The synthetic source's history is GBM: no regimes, so BIC keeps one.
    app, window = qt_app
    lab = _markov_lab(app, window, mode="Market history", count="Auto (BIC)",
                      source="synthetic", symbol="SPY", lookback="600")
    run = lab._run_result
    k = run.fit.model.n_regimes
    assert [f.model.n_regimes for f in run.fits] == [1, 2, 3] and run.truth is None
    table = lab._stats[k]
    assert table.isVisibleTo(lab) and not table._truth_header.isVisibleTo(lab)
    assert "lowest" in table.value_text(f"bic{k}")
    assert [p.title.split(" ·")[0] for p in lab._chart._panels] == [
        "Price", "Filtered probabilities"]
    assert "SPY" in lab._chart_card.subtitle.full_text()
    assert "adjusted" in lab._chart_card.subtitle.full_text()     # the default basis (D38)
    assert lab._transitions.rowCount() == k


def test_markov_lab_fits_the_chosen_price_basis(qt_app):
    app, window = qt_app
    lab = _markov_lab(app, window, mode="Market history", source="synthetic", symbol="SPY",
                      lookback="400", basis="Traded")
    assert lab._basis.current() == "Traded"
    assert " traded " in lab._chart_card.subtitle.full_text()
    lab.on_hide()
    assert window._core.state_store.load("markov_lab")["basis"] == "Traded"


def test_markov_lab_notes(qt_app):
    from dataclasses import replace

    from quant_hub.models.markov import fit_hmm

    app, window = qt_app
    # Two regimes fitted to the three-regime world: no TRUE column to line up.
    lab = _markov_lab(app, window, world="Three regimes")
    assert lab._finding.isVisibleTo(lab) and "this world has 3" in lab._finding._text.text()
    assert not lab._stats[2]._truth_header.isVisibleTo(lab)
    assert not lab._warning.isVisibleTo(lab)
    # EM stopped short, and a regime on the variance floor.
    run = lab._run_result
    stopped = replace(fit_hmm(run.returns, 2, max_iterations=2), at_floor=(0,))
    assert not stopped.converged
    lab._on_done(replace(run, fit=stopped, fits=(stopped,)))
    assert lab._warning.isVisibleTo(lab) and "without converging" in lab._warning._text.text()
    assert "variance floor" in lab._finding._text.text()
    assert lab._stats[2].value_text("iterations").endswith("(stopped)")


# ----------------------------------------------------------------- Portfolio
def _portfolio(app, window, **state):
    """The Portfolio page priced from the synthetic source (no IBKR in tests)."""
    window._core.state_store.save("portfolio", {"source": "synthetic", **state})
    page = window.show_page("Portfolio")
    wait_for(app, lambda: page._view is not None, timeout_s=30)
    return page


def test_portfolio_shows_the_example_before_any_import(qt_app):
    app, window = qt_app
    page = _portfolio(app, window)
    view = page._view
    assert view.snapshot.source == "Example"
    assert page._finding.isVisibleTo(page) and "example portfolio" in page._finding._text.text()
    assert page._tiles["var"]._value.full_text().startswith("$")
    assert page._table.rowCount() == len(view.rows) + 1             # and a cash line
    shares = [float(page._table.item(r, 4).text().rstrip("%")) for r in range(len(view.rows))]
    assert shares == sorted(shares, reverse=True)                   # led by risk


def test_portfolio_regressions_rolling_beta_and_links(qt_app):
    app, window = qt_app
    page = _portfolio(app, window, lags="Off")
    reg = page._view.regressions
    assert reg is not None and reg.dimson_lags == 0
    table = page._regress_table
    assert table.rowCount() == len(reg.fits)
    assert table.item(table.rowCount() - 1, 0).text() == "Portfolio"
    assert table.columnCount() == 5                                  # fits at 1280 px
    assert not page._exposure_table.isVisibleTo(page)                # no factors, no lag
    assert page._rolling_stack.currentWidget() is page._rolling      # the portfolio's
    page._on_regression_row(0)
    assert page._rolling_key == page._regression_keys[0]
    n = len(page._view.report.symbols)
    assert page._links.rowCount() == n * (n - 1) // 2
    page._lags.select.set_current("1 day")
    wait_for(app, lambda: page._view.regressions.dimson_lags == 1, timeout_s=30)
    others = page._exposure_table
    assert others.isVisibleTo(page) and others.horizontalHeaderItem(1).text() == "Lag β"
    assert others.item(0, 1).text() != "—"
    assert page._rolling_key == page._regression_keys[0]             # the choice is kept


def test_portfolio_growth_sleeves_and_what_if(qt_app):
    app, window = qt_app
    page = _portfolio(app, window)
    growth = page._view.growth
    assert growth is not None
    assert page._sleeves.rowCount() == len(growth.sleeves)
    assert page._growth_tiles["growth"].value_text().endswith("%")
    assert not page._book_card.isVisibleTo(page)                     # the example holds no options
    page._on_sleeve_row(1)
    assert page._what_sleeve.current() == page._sleeve_names[1]
    assert "today's size" in page._what_result.text()
    page._what_scale.set_text("lots")
    page._what_scale.committed.emit()
    assert "must be a number" in page._what_result.text()


def test_portfolio_options_book_appears_with_options(qt_app, tmp_path):
    app, window = qt_app
    page = _portfolio(app, window)
    path = tmp_path / "book.csv"
    path.write_text("symbol,quantity,price,account\nSPY,10,500,Brokerage\n"
                    "-SPY271217P450,2,12.50,Brokerage\n", encoding="utf-8")
    page.import_file(path)
    wait_for(app, lambda: page._view.snapshot.name == "book.csv", timeout_s=30)
    assert page._book_card.isVisibleTo(page)
    assert page._view.book.contracts == 1 and page._view.book.delta < 0
    assert page._book_tiles["carry"].value_text().endswith("%")


def test_portfolio_extra_factors_add_beta_columns(qt_app):
    app, window = qt_app
    page = _portfolio(app, window, lags="Off")
    table = page._regress_table
    page._factors.set_text("tlt, gld")
    page._factors.committed.emit()
    wait_for(app, lambda: page._view.regressions.factors == ("TLT", "GLD"), timeout_s=30)
    assert table.horizontalHeaderItem(2).text() == "β SPY"
    others = page._exposure_table
    assert [others.horizontalHeaderItem(c).text() for c in range(others.columnCount())] == [
        "Holding", "β TLT", "β GLD"]                                 # lag off: no lag column
    assert others.rowCount() == table.rowCount()
    page._factors.set_text("QQQ TLT GLD IWM")
    page._factors.committed.emit()
    assert "at most 3" in page._header._status.full_text()


def test_portfolio_imports_a_fidelity_file_and_splits_accounts(qt_app, tmp_path):
    from test_positions_csv import FIDELITY
    app, window = qt_app
    page = _portfolio(app, window)
    path = tmp_path / "Portfolio_Positions.csv"
    path.write_text(FIDELITY, encoding="utf-8")
    page.import_file(path)
    wait_for(app, lambda: page._view.snapshot.source == "Fidelity", timeout_s=30)
    assert page._header._status.full_text().startswith("Imported Portfolio_Positions.csv")
    assert page._warning.isVisibleTo(page) and "expired" in page._warning._text.text()
    page._accounts.select.set_current("ROTH IRA")
    wait_for(app, lambda: page._view.account == "ROTH IRA", timeout_s=30)
    assert [r.symbol for r in page._view.rows] == ["QQQ"]


def test_portfolio_imports_one_export_per_account_together(qt_app, tmp_path):
    from test_positions_csv import view_export
    app, window = qt_app
    page = _portfolio(app, window)
    paths = []
    for name, account in (("ind.csv", "Individual"), ("roth.csv", "ROTH IRA")):
        paths.append(tmp_path / name)
        paths[-1].write_text(view_export(account), encoding="utf-8")
    page.import_files(paths)
    wait_for(app, lambda: page._view.snapshot.name == "ind.csv + roth.csv", timeout_s=30)
    assert page._header._status.full_text().startswith("Imported ind.csv + roth.csv")
    assert "in 2 accounts" in page._header._status.full_text()
    page._accounts.select.set_current("ROTH IRA")
    wait_for(app, lambda: page._view.account == "ROTH IRA", timeout_s=30)

def test_portfolio_offline_uses_the_store_and_flags_a_deciding_day(tmp_path):
    """V0 end to end: IBKR isn't connected, so the page runs on the stored,
    covered bars and says so; one planted +300% day is flagged, the numbers
    without it shown, and the histogram counts it at its edge."""
    from quant_hub.data.ranges import DateRange
    db = tmp_path / "market_data.db"
    store = BarStore(db)
    dates = pd.bdate_range("2025-01-02", periods=300)
    rng = np.random.default_rng(5)
    for symbol in ("AAA", "BBB", "SPY"):
        log_returns = rng.normal(0.0, 0.012, len(dates))
        if symbol == "BBB":
            log_returns[150] = np.log(4.0)
        closes = 50 * np.exp(np.cumsum(log_returns))
        frame = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                              "volume": np.full(len(dates), 1e6)}, index=dates)
        store.insert_bars(InstrumentKey(symbol), "ibkr", "1 day", frame)
        store.add_coverage(InstrumentKey(symbol), "ibkr", "1 day",
                           DateRange(dates[0], dates[-1]))
    store.close()
    app, window = create_app(AppConfig(db_path=db), argv=["test"])
    window.show()
    try:
        window._core.state_store.save("portfolio", {"source": "ibkr", "mode": "Imported"})
        page = window.show_page("Portfolio")
        positions = tmp_path / "positions.csv"
        positions.write_text("symbol,quantity,price\nAAA,100,50\nBBB,10,50\n", encoding="utf-8")
        page.import_file(positions)
        wait_for(app, lambda: page._view is not None
                 and page._view.snapshot.source != "Example", timeout_s=30)
        view = page._view
        assert view.offline_reason == "not connected to IBKR"
        assert view.report.pnl.index[-1] == dates[-1]
        assert view.sessions_missed > 0                       # the bars end months ago
        assert "offline" in page._warning._text.text()
        (day,) = view.report.influence.flagged
        assert day.driver == "BBB" and day.date == dates[150]
        assert page._without.isVisibleTo(page)
        assert "BBB" in page._influence_text.text()
        below, above = page._losses._beyond
        assert len(below) + len(above) == 1
    finally:
        _close(app, window)



# ----------------------------------------------------------------- Valuation
def _valuation(app, window, **state):
    """The Valuation page over a stored filer (no SEC identity in tests, so the
    store serves): the made-up DEMO filings, ten shares."""
    from datetime import datetime
    from test_sec_edgar import companyfacts, flow
    from quant_hub.providers.sec_edgar import facts_from_companyfacts
    core = window._core
    now = datetime.now()
    shares = {"WeightedAverageNumberOfDilutedSharesOutstanding": {"shares": [
        flow(2025, 10.0, "A25")]}}
    core.fundamentals_store.save_tickers({"DEMO": (1234, "DEMO Corp")}, now)
    core.fundamentals_store.save_facts(1234, "DEMO Corp",
                                       facts_from_companyfacts(companyfacts(**shares)), now)
    core.state_store.save("valuation", {"price_source": "synthetic", **state})
    page = window.show_page("Valuation")
    page._identifier.set_text("DEMO")
    page._load(False)
    wait_for(app, lambda: page._result is not None, timeout_s=30)
    return page


def test_valuation_loads_values_prices_saves_and_reopens(qt_app):
    app, window = qt_app
    page = _valuation(app, window)
    assert page._identity_note.isVisibleTo(page)                      # no identity here
    assert page._tiles["value"].value_text().startswith("$")
    assert page._filings.rowCount() == 6                               # five years + trailing
    assert page._projection.rowCount() == 6                            # five years + terminal
    assert page._fields["growth"].text() == "5.7"                      # seeded: the CAGR
    page._get_price()
    wait_for(app, lambda: page._tiles["margin"].value_text() != "—", timeout_s=30)
    assert page._price.text() and "synthetic" in page._price_caption.text()
    assert page._implied.rowCount() == 2
    assert page._implied.item(0, 0).text() == "Flat revenue growth"
    assert page._sources.isVisibleTo(page)                             # provenance, side panel
    page._save()
    wait_for(app, lambda: page._saved.rowCount() == 1 and page._saved_ids, timeout_s=30)
    page._fields["growth"].set_text("1")
    page._reopen(0)
    wait_for(app, lambda: page._saved_note.isVisibleTo(page), timeout_s=30)
    assert "unchanged" in page._saved_note._text.text()
    assert page._fields["growth"].text() == "5.7"                      # the saved assumption


def test_valuation_per_year_paths_overrides_and_bad_input(qt_app):
    app, window = qt_app
    page = _valuation(app, window)
    page._fields["growth"].set_text("10, 8, 6, 4, 2")
    page._recompute()
    assert page._assumed.revenue_growth == pytest.approx([0.10, 0.08, 0.06, 0.04, 0.02])
    before = page._result.value_per_share
    page._fields["net_debt"].set_text("0")
    page._recompute()
    assert page._result.value_per_share > before                       # net debt was 130
    page._fields["growth"].set_text("10 8")
    page._recompute()
    assert "2 values for 5 years" in page._header._status.full_text()


def test_valuation_screen_and_export(qt_app, tmp_path):
    app, window = qt_app
    page = _valuation(app, window)
    page._screen_list.set_text("DEMO NOPE")
    page._run_screen()
    wait_for(app, lambda: page._screen.rowCount() == 2, timeout_s=30)
    assert page._screen.item(0, 0).text() == "DEMO"
    path = tmp_path / "screen.csv"
    page.export_screen(path)
    wait_for(app, lambda: path.exists(), timeout_s=30)
    assert path.read_text(encoding="utf-8").splitlines()[0].startswith("ticker,company")

def test_portfolio_from_ibkr_while_disconnected_says_how_to_connect(qt_app):
    """E1: the button reads the login's accounts; without a connection the
    page says to connect rather than failing quietly."""
    app, window = qt_app
    page = _portfolio(app, window)
    assert page._broker_button.isVisibleTo(page)
    page.load_from_broker()
    wait_for(app, lambda: page._broker_button.isEnabled()
             and "IBKR" in page._header._status.full_text(), timeout_s=30)
    status = page._header._status.full_text()
    assert "not connected" in status and "Press Connect IBKR" in status


def test_portfolio_from_ibkr_keeps_and_analyses_the_read(qt_app):
    """E1 end to end through a stand-in for the account service: the read is
    kept as a snapshot, analysed, and what IBKR's read left out is named."""
    from datetime import datetime

    from quant_hub.broker.types import AccountSummary, BrokerPosition
    from quant_hub.services.accounts import broker_snapshot

    class StandIn:
        def snapshot(self):
            positions = [BrokerPosition("DU0000001", "AAA", "STK", 100, 20.0, 1, "USD"),
                         BrokerPosition("DU0000001", "ESZ6", "FUT", 1, 1.0, 3, "USD")]
            return broker_snapshot(["DU0000001"], positions,
                                   [AccountSummary("DU0000001", "USD", 9_000.0, 1_000.0,
                                                   8_000.0)], datetime.now())

    app, window = qt_app
    page = _portfolio(app, window)
    page._broker_accounts = StandIn()
    page.load_from_broker()
    wait_for(app, lambda: page._view is not None and page._view.snapshot.source == "IBKR",
             timeout_s=30)
    assert page._header._status.full_text().startswith("Read from IBKR: 2 positions")
    assert "net liquidation $9,000" in page._header._status.full_text()
    assert [r.symbol for r in page._view.rows] == ["AAA"]
    assert page._warning.isVisibleTo(page) and "ESZ6" in page._warning._text.text()


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


def test_options_lab_prices_from_a_saved_chain_surface(qt_app):
    # C4 offline: a saved chain (synthetic, with parity pairs) becomes the
    # surface every contract is priced from.
    import math

    from quant_hub.models.black_scholes import price_array
    from quant_hub.models.svi import SSVISurface
    from quant_hub.models.vol_surface import SyntheticChainSpec, synthetic_chain

    app, window = qt_app
    truth = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)
    chain = synthetic_chain(truth, SyntheticChainSpec(rate=0.045, dividend_yield=0.015,
                                                      noise=0.0, parity_pairs=5))
    window._core.chain_store.save(chain)
    lab = _manual_lab(app, window)
    card = lab._underlying
    card._symbol.set_text("SYNTH")
    card._vol_modes.set_current("Surface")
    card._on_vol_mode("Surface")
    wait_for(app, lambda: card.surface() is not None)
    assert card._badge.text() == "SURFACE" and not card._vol.isEnabled()
    assert card._spot.text() == "100.00"

    pricer = lab._pricer
    pricer._strike.set_text("95")
    pricer._dte.set_text("91")
    pricer._on_mode("Price from σ")
    point = card.surface().point(95.0, 91.0)
    years = 91 / 365
    true_vol = float(truth.slice(years).implied_vol(math.log(95.0 / point.forward), years))
    assert point.vol == pytest.approx(true_vol, abs=2e-4)
    expected = float(price_array(100.0, 95.0, 91.0, point.rate, point.vol, "call",
                                 point.dividend_yield))
    assert float(pricer._hero.text()) == pytest.approx(expected, abs=1e-4)
    assert "surface" in pricer._stats.value_text("vol")

    lab._on_tab("Strategy")
    strategy = lab._strategy
    strategy.refresh()
    sigmas = [strategy._table.item(r, 4).text() for r in range(strategy._table.rowCount())]
    assert sigmas and all(s.endswith("%") for s in sigmas)
    # A new symbol's surface re-centres the legs, so none sits in a far wing.
    surface = card.surface()
    assert all(not surface.point(leg.strike, leg.dte).extrapolated for leg in strategy._legs)

    card._vol_modes.set_current("Flat σ")
    card._on_vol_mode("Flat σ")
    assert card.surface() is None and card._vol.isEnabled()


def test_options_lab_surface_recentres_strikes_built_at_another_price(qt_app):
    # Live C4 finding: the saved state said "centred on SPY", but that was
    # synthetic SPY at $385, and the real chain's SPY is at $771. A same-name
    # surface far from the strikes' spot must still re-centre them.
    from quant_hub.models.strategy import OptionLeg
    from quant_hub.models.svi import SSVISurface
    from quant_hub.models.vol_surface import SyntheticChainSpec, synthetic_chain

    app, window = qt_app
    truth = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)
    window._core.chain_store.save(synthetic_chain(truth, SyntheticChainSpec(parity_pairs=5)))
    lab = _manual_lab(app, window, spot="385")
    card, strategy = lab._underlying, lab._strategy
    strategy._apply_template()                      # strikes built around $385
    card._centred_on, card._centred_at = "SYNTH", 385.0
    card._symbol.set_text("SYNTH")

    def load_surface():
        card._vol_modes.set_current("Surface")
        card._on_vol_mode("Surface")
        wait_for(app, lambda: card.surface() is not None)
        lab._on_tab("Strategy")
        strategy.refresh()

    load_surface()
    surface = card.surface()
    assert all(0.8 * surface.spot < leg.strike < 1.2 * surface.spot for leg in strategy._legs)
    assert all(not surface.point(leg.strike, leg.dte).extrapolated for leg in strategy._legs)
    assert "strikes re-centred on spot" in lab._header._status.full_text()

    # A far-wing leg is priced, and its vol is flagged as extrapolated.
    strategy._legs.append(OptionLeg("put", 40.0, 30.0, 1))
    strategy.refresh()
    flagged = strategy._table.item(strategy._table.rowCount() - 1, 4)
    assert flagged.text().endswith(" *") and "Extrapolated" in flagged.toolTip()
    assert not strategy._table.item(0, 4).text().endswith("*")

    # Reloading the same symbol at the same spot keeps the user's strikes.
    card._vol_modes.set_current("Flat σ")
    card._on_vol_mode("Flat σ")
    load_surface()
    assert strategy._legs[-1].strike == 40.0
    assert "re-centred" not in lab._header._status.full_text()


def test_options_lab_surface_without_a_saved_chain_says_where_to_get_one(qt_app):
    app, window = qt_app
    lab = _manual_lab(app, window)
    card = lab._underlying
    card._symbol.set_text("QQQ")
    card._on_vol_mode("Surface")
    wait_for(app, lambda: "Volatility Lab" in lab._header._status.full_text())
    assert card.surface() is None


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
    for view, widget in (("Term structure", lab._term), ("Surface", lab._surface_view),
                         ("Smiles", lab._smiles)):
        lab._on_view(view)
        assert lab._stack.currentWidget() is widget
    assert lab._surface_stack.currentWidget() is lab._surface   # heatmap by default
    _hover(lab._surface._canvas, 0.4, 0.5)
    assert "IV" in lab._surface._readout.text() and "k/√θ" in lab._surface._readout.text()


def test_vol_lab_3d_toggle(qt_app):
    # Wiring only: Chromium's drawing is checked in a fresh process below.
    app, window = qt_app
    lab = _fitted_vol_lab(app, window)
    assert lab._mesh is not None and lab._mesh.true_iv is not None
    assert lab._checks.value_text("mesh_butterfly").startswith("ok")
    assert lab._checks.value_text("mesh_calendar").startswith("ok")
    lab._on_view("Surface")
    lab._surface_modes.set_current("3D")
    lab._on_surface_mode("3D")
    surface = lab._surface3d
    assert surface is not None and lab._surface_stack.currentWidget() is surface
    assert "fitted expiries" in lab._mesh_label.text()
    lab._on_surface_mode("Heatmap")
    assert lab._surface_stack.currentWidget() is lab._surface


_DRAW_3D = """
import sys, time
from pathlib import Path
from quant_hub.app import create_app
from quant_hub.config import AppConfig

app, window = create_app(AppConfig(db_path=Path(sys.argv[1])), argv=["test"])
window.show()
native_id = window.winId()
lab = window.show_page("Volatility Lab")
deadline = time.perf_counter() + 90
def wait(condition):
    while not condition():
        if time.perf_counter() > deadline:
            print("timed out"); sys.exit(1)
        app.processEvents(); time.sleep(0.005)
wait(lambda: lab._fit is not None)
lab._on_view("Surface")
lab._on_surface_mode("3D")
surface = lab._surface3d
drawn = []
surface.rendered.connect(lambda: drawn.append(True))
wait(lambda: drawn or not surface._loading._title.text().startswith("Loading"))
assert window.winId() == native_id, "first 3D view recreated the main window"
outcome = "drawn" if drawn else surface._loading._body.text()
print("RESULT: " + " ".join(outcome.split()))
app.aboutToQuit.emit()
"""


def test_vol_lab_3d_draws_in_a_fresh_process(tmp_path):
    # Offscreen Chromium is most reliable for the first web view in a
    # process (later views in a long test run can lose WebGL), so the real
    # draw runs in its own process; the app itself has one window. Even then,
    # offscreen WebGL sometimes can't compile shaders: that's the test
    # environment, so it skips with the reason. A hang, or any other plotly
    # error, fails.
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", _DRAW_3D, str(tmp_path / "market_data.db")],
        capture_output=True, text=True, timeout=180,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    outcome = next((line[len("RESULT: "):] for line in result.stdout.splitlines()
                    if line.startswith("RESULT: ")), None)
    assert outcome is not None, result.stdout + result.stderr      # e.g. timed out
    if outcome != "drawn" and ("WebGL" in outcome or "shader" in outcome):
        pytest.skip(f"no working WebGL offscreen here: {outcome[:120]}")
    assert outcome == "drawn"


def test_vol_lab_3d_needs_two_expiries(qt_app):
    app, window = qt_app
    lab = _fitted_vol_lab(app, window)
    first = lab._fit
    lab._fields["expiries"].set_text("30")
    lab._run()
    wait_for(app, lambda: lab._fit is not first)
    assert lab._mesh is None
    lab._on_view("Surface")
    lab._on_surface_mode("3D")
    assert lab._surface_stack.currentWidget() is lab._surface_empty
    assert "two fitted expiries" in lab._surface_empty._body.text()
    assert lab._checks.value_text("mesh_butterfly") == "—"


def test_surface_figure_is_plain_json():
    import json
    from quant_hub.gui.surface3d import surface_figure
    from quant_hub.models.svi import SSVISurface
    from quant_hub.models.vol_surface import (SyntheticChainSpec, fit_surface, surface_mesh,
                                              synthetic_chain)
    truth = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)
    mesh = surface_mesh(fit_surface(synthetic_chain(truth, SyntheticChainSpec(seed=2))), truth)
    figure = surface_figure(mesh)
    text = json.dumps(figure, allow_nan=False)      # NaN would not be valid JSON
    assert [t["name"] for t in figure["data"]] == [
        "SVI surface", "Fitted expiries", "True SSVI", "Bid–ask", "Quotes (mid)"]
    surface = figure["data"][0]
    assert len(surface["z"]) == len(mesh.dte) and len(surface["z"][0]) == len(mesh.z)
    assert figure["layout"]["scene"]["uirevision"] == "surface"
    assert len(text) < 400_000


def test_vol_lab_market_chain_from_stored_snapshot(qt_app):
    # Market mode end to end without a broker: a saved snapshot (here a
    # synthetic chain with parity pairs and provider IVs) through "stored".
    from dataclasses import replace

    from quant_hub.models.svi import SSVISurface
    from quant_hub.models.vol_surface import SyntheticChainSpec, synthetic_chain

    app, window = qt_app
    truth = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)
    chain = synthetic_chain(truth, SyntheticChainSpec(parity_pairs=5, dividend_yield=0.02))
    chain = replace(chain, quotes=tuple(replace(q, reference_iv=0.2) for q in chain.quotes))
    snapshot = window._core.chain_store.save(chain)

    lab = _fitted_vol_lab(app, window)
    lab._on_mode("Market chain")
    assert lab._groups["Market chain"].isVisibleTo(lab)
    assert not lab._groups["Synthetic"].isVisibleTo(lab)
    lab._chain_source.select.set_current("stored")
    lab._fields["chain_symbol"].set_text("SYNTH")
    first = lab._fit
    lab._run()
    wait_for(app, lambda: lab._fit is not first)
    assert lab._truth is None and lab._snapshot == snapshot
    assert lab._table.horizontalHeaderItem(2).text() == "IBKR"
    assert lab._table.item(0, 2).text() == "20.00%"
    assert lab._carry_card.isVisibleTo(lab) and lab._carry_table.rowCount() == 6
    assert "snapshot #" in lab._header._status.full_text()
    assert lab._checks.value_text("itm") == "30"            # 6 expiries x 5 ITM sides
    assert lab._mesh is not None and lab._mesh.true_iv is None

    lab._on_mode("Synthetic")
    second = lab._fit
    lab._run()
    wait_for(app, lambda: lab._fit is not second)
    assert lab._table.horizontalHeaderItem(2).text() == "True"
    assert not lab._carry_card.isVisibleTo(lab)


def test_vol_lab_fit_method_toggle_refits_the_same_chain(qt_app):
    app, window = qt_app
    lab = _fitted_vol_lab(app, window)
    assert lab._method == "SVI" and lab._fit.method == "svi"     # the default (D35)
    chain = lab._fit.chain
    lab._methods.set_current("SSVI")
    lab._on_method("SSVI")
    wait_for(app, lambda: lab._fit.method == "ssvi")
    assert lab._fit.chain is chain                                # refit, not refetched
    assert lab._fit_card.subtitle.full_text().startswith("Joint SSVI")
    assert lab._fit.butterfly_free and lab._fit.calendar_free
    lab._on_method("SVI")
    wait_for(app, lambda: lab._fit.method == "svi")
    assert lab._fit_card.subtitle.full_text().startswith("Independent SVI")


def test_vol_lab_stored_without_snapshot_says_so(qt_app):
    app, window = qt_app
    lab = _fitted_vol_lab(app, window)
    lab._on_mode("Market chain")
    lab._chain_source.select.set_current("stored")
    lab._fields["chain_symbol"].set_text("QQQ")
    lab._run()
    wait_for(app, lambda: "no saved QQQ chain" in lab._header._status.full_text())
    assert all(w.isEnabled() for w in lab._run_buttons())


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


# -------------------------------------------------------------------- Review
def _review(app, window, tmp_path):
    """The Review page over made-up holdings: two stored exports (the later
    one adds to BBB and buys CCC), two years of "file" bars ending today with
    CCC's planted jump, and the review tests' rules at the fixture's path."""
    from test_review_service import RULES, bars, export
    core = window._core
    bars(core.store, end=pd.Timestamp.today().normalize())
    for name, day, holdings, cash in (
            ("older.csv", "10/15/2026", {"AAA": 40, "BBB": 10}, 4000),
            ("newer.csv", "10/22/2026", {"AAA": 40, "BBB": 20, "CCC": 10}, 3000)):
        path = tmp_path / name
        path.write_text(export(day, holdings, cash), encoding="utf-8")
        core.portfolio.import_positions(path)
    core.config.regime_path.write_text(RULES, encoding="utf-8")
    page = window.show_page("Review")
    wait_for(app, lambda: page._rules is not None, timeout_s=30)
    return page


def test_review_runs_the_checklist_and_keeps_the_journal(qt_app, tmp_path):
    app, window = qt_app
    page = _review(app, window, tmp_path)
    assert page._version_note.isVisibleTo(page) and "Version 1" in page._version_note._text.text()
    assert list(page._ticks) == ["credit calm"]
    page._ticks["credit calm"].setChecked(True)
    page._run()
    wait_for(app, lambda: page._last is not None, timeout_s=90)
    assert page._header._status.full_text().startswith(f"Review #{page._last.review_id}")
    assert int(page._tiles["fail"].value_text()) >= 1                  # AAA over the 25% cap
    assert page._checklist.rowCount() > 10
    first = [page._checklist.item(0, c).text() for c in range(page._checklist.columnCount())]
    assert first[3].startswith("fail")                                 # breaches first
    assert page._streams.rowCount() == 2 and page._dominance.rowCount() == 1
    assert page._dominance.item(0, 0).text() == "CCC"
    assert page._dominance_card.subtitle.full_text().startswith("Graded without")
    assert page._market.rowCount() == 3                                 # calm, volatility, indicator
    assert [page._drafts.item(r, 0).text() for r in range(page._drafts.rowCount())] == ["BBB", "CCC"]
    wait_for(app, lambda: page._history.rowCount() == 1, timeout_s=30)

    page._draft_to_form(1)
    assert (page._symbol.text(), page._quantity.text()) == ("CCC", "10")
    page._reason.set_text("opened the second stream")
    page._record_trade()
    wait_for(app, lambda: page._trades.rowCount() == 1 and page._trades.item(0, 1) is not None
             and page._trades.item(0, 1).text() == "CCC", timeout_s=30)
    page._notes.setPlainText("first review")
    page._save_notes()
    wait_for(app, lambda: page._header._status.full_text() == "Notes saved.", timeout_s=30)
    assert window._core.regime_store.review(page._last.review_id).notes == "first review"


def test_review_names_an_unreadable_rules_line(qt_app, tmp_path):
    from test_review_service import RULES
    from test_regime_md import row
    app, window = qt_app
    window._core.config.regime_path.write_text(
        RULES.replace(row("weight", "25", "fail"), row("weight", "25", "maybe")), encoding="utf-8")
    page = window.show_page("Review")
    wait_for(app, lambda: page._error_note.isVisibleTo(page), timeout_s=30)
    assert "REGIME.md line" in page._error_note._text.text()
    assert page._rules is None
