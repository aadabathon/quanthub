"""Saved evidence survives data changes, migrations, and app restarts."""
from dataclasses import replace
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.sources import SyntheticSource
from quant_hub.data.types import PriceBasis, PriceHistory
from quant_hub.models.strategies import StrategySpec
from quant_hub.services.market_service import MarketService
from quant_hub.services.research import Experiment, ResearchService, WORLD_SOURCE
from quant_hub.storage.research_store import ResearchStore
from quant_hub.storage.schema import prepare_store


@pytest.fixture
def service(tmp_path):
    db = tmp_path / "research.db"
    prepare_store(db)
    store = ResearchStore(db)
    yield ResearchService(MarketService([SyntheticSource()]), store), store, db
    store.close()


def small_experiment():
    return Experiment(strategy=StrategySpec(lookback=10, train_size=60, refit_every=100), observations=180)


def test_reopen_uses_exact_prices_decisions_costs_and_notes(service, monkeypatch):
    research, store, db = service
    run = research.run(small_experiment())
    research.save_notes(run.id, "Reject until independently replicated")
    monkeypatch.setattr(research, "prices", lambda _: pytest.fail("reopen must not fetch or refit"))
    reopened = research.reopen(run.id)
    np.testing.assert_allclose(reopened.result.equity, run.result.equity, rtol=0, atol=0)
    assert reopened.notes.startswith("Reject")
    assert len(reopened.payload["prices"]["dates"]) == 180
    assert len(reopened.result.net_returns) == 119
    assert reopened.payload["evidence_sha256"] == run.payload["evidence_sha256"]
    assert not reopened.code_changed
    with sqlite3.connect(db) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM research_runs").fetchone()[0])
        payload["prices"]["values"][0][0] += 1
        conn.execute("UPDATE research_runs SET payload=?", (json.dumps(payload),))
    with pytest.raises(ValueError, match="fingerprint"):
        research.reopen(run.id)


def test_benchmark_is_buy_hold_with_same_entry_fee(service):
    research, _, _ = service
    run = research.run(replace(small_experiment(), symbols=("A", "B")))
    assert run.benchmark.fees.iloc[0] == pytest.approx(20 / 1.0002)
    np.testing.assert_allclose(run.benchmark.trades.iloc[1:], 0, atol=1e-8)
    assert run.result.net_returns.index.equals(run.benchmark.net_returns.index)


def test_changed_implementation_is_disclosed_without_refitting_saved_decisions(service, monkeypatch):
    import quant_hub.services.research as module
    research, _, _ = service
    run = research.run(small_experiment())
    monkeypatch.setattr(module, "source_fingerprint", lambda: "changed-source")
    reopened = research.reopen(run.id)
    assert reopened.code_changed
    np.testing.assert_allclose(reopened.result.equity, run.result.equity, rtol=0, atol=0)


@pytest.mark.parametrize("name", ["Kalman trend", "Kalman level", "GARCH volatility", "Markov calm"])
def test_fitted_model_parameters_and_states_survive_json_replay(service, name):
    research, _, _ = service
    spec = StrategySpec(name=name, lookback=10, threshold=.5, train_size=110)
    run = research.run(replace(small_experiment(), strategy=spec))
    assert all("parameters" in fit for fit in run.payload["fits"])
    reopened = research.reopen(run.id)
    np.testing.assert_allclose(reopened.result.equity, run.result.equity, rtol=0, atol=0)


def test_sweep_records_every_trial_and_explicit_selection_assumption(service):
    research, _, _ = service
    result = research.sweep(small_experiment(), [5, 10], [0, .02], 3)
    grid = result["sweep"]
    assert len(grid["run_ids"]) == 4 and len(research.list_runs()) == 5
    assert grid["independent_trials"] == 3
    assert "not an untouched" in grid["selection_warning"]
    windows = [research.reopen(i).result.prices.index for i in grid["run_ids"]]
    assert all(w.equals(windows[0]) for w in windows)
    manifest = research.reopen(result["best"].id)
    assert manifest.payload["sweep"] == grid


def test_paper_import_is_copied_deduplicated_editable_and_linkable(service, tmp_path):
    research, store, _ = service
    assert len(research.papers()) == 5
    path = tmp_path / "my paper.pdf"
    path.write_bytes(b"%PDF-1.4\n%test fixture")
    paper = research.save_paper({"title": "My paper", "run_ids": ["some-run"]}, str(path))
    saved_path = research.paper_path(paper)
    path.unlink()
    assert saved_path.endswith(".pdf")
    research.save_paper({**paper, "notes": "Replication pending", "status": "Reading"})
    assert len(research.papers()) == 6
    reloaded = next(p for p in research.papers() if p["id"] == paper["id"])
    assert reloaded["notes"] == "Replication pending" and reloaded["run_ids"] == ["some-run"]
    with pytest.raises(ValueError, match="missing"):
        research.paper_path({**paper, "pdf": "../escape.pdf"})


def test_v2_migration_preserves_existing_data_and_copies_first(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT, backup TEXT)")
        conn.execute("INSERT INTO schema_migrations VALUES (2,'old',NULL)")
        conn.execute("CREATE TABLE owner_data (note TEXT)")
        conn.execute("INSERT INTO owner_data VALUES ('keep')")
    migrated = prepare_store(path)
    assert migrated.version == 3 and migrated.backup.is_file()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT note FROM owner_data").fetchone()[0] == "keep"
        assert conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0] == 0
    with sqlite3.connect(migrated.backup) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='research_runs'").fetchall()


def test_market_panel_uses_explicit_basis_intersection_and_no_fills(service):
    _, store, _ = service
    dates = pd.bdate_range("2025-01-02", periods=180)
    requested = []
    class Source:
        name = "fixture"
        def get_history(self, symbol, start, end, basis):
            requested.append((symbol, basis))
            index = dates if symbol == "A" else dates.delete([10, 21, 100])
            return PriceHistory(symbol, self.name, pd.DataFrame({"close": np.linspace(90, 120, len(index))}, index=index), basis)
    research = ResearchService(MarketService([Source()]), store)
    experiment = replace(small_experiment(), source="fixture", symbols=("A", "B"), basis="adjusted")
    prices, provenance = research.prices(experiment)
    assert requested == [("A", PriceBasis.ADJUSTED), ("B", PriceBasis.ADJUSTED)]
    assert len(prices) == 177 and dates[10] not in prices.index
    assert provenance["excluded_unmatched_sessions"] == 3
    run = research.run(experiment)
    assert run.payload["provenance"]["basis"] == "adjusted"
    np.testing.assert_allclose(research.reopen(run.id).result.equity, run.result.equity)


def test_reimporting_the_same_pdf_keeps_its_metadata_and_notes(service, tmp_path):
    research, _, _ = service
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4\n% same bytes")
    first = research.save_paper({"title": "Original title", "notes": "Keep my notes"}, str(path))
    second = research.save_paper({"title": "File title"}, str(path))
    assert first["id"] == second["id"] and second["notes"] == "Keep my notes"
    assert sum(p.get("pdf") == first["pdf"] for p in research.papers()) == 1
