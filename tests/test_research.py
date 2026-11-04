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
from quant_hub.storage.schema import SCHEMA_VERSION, prepare_store


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
    canon = len(research.canon.papers)
    assert len(research.papers()) == canon
    path = tmp_path / "my paper.pdf"
    path.write_bytes(b"%PDF-1.4\n%test fixture")
    paper = research.save_paper({"title": "My paper", "run_ids": ["some-run"]}, str(path))
    saved_path = research.paper_path(paper)
    path.unlink()
    assert saved_path.endswith(".pdf")
    research.save_paper({**paper, "notes": "Replication pending", "status": "Reading"})
    assert len(research.papers()) == canon + 1
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
    assert migrated.version == SCHEMA_VERSION and migrated.backup.is_file()
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


def test_split_sized_gaps_are_saved_with_the_run(service):
    # D57: a series with a split left in is named in the run's provenance.
    _, store, _ = service
    dates = pd.bdate_range("2025-01-02", periods=180)
    class Source:
        name = "fixture"
        def get_history(self, symbol, start, end, basis):
            close = np.linspace(90, 120, len(dates))
            if symbol == "B":
                close[90:] /= 2
            return PriceHistory(symbol, self.name, pd.DataFrame({"close": close}, index=dates), basis)
    research = ResearchService(MarketService([Source()]), store)
    experiment = replace(small_experiment(), source="fixture", symbols=("A", "B"), basis="adjusted")
    _, provenance = research.prices(experiment)
    ((day, gap, ratio),) = provenance["split_gaps"]["B"]
    assert (day, ratio) == (dates[90].date().isoformat(), "2:1") and gap == pytest.approx(0.5, abs=0.01)
    assert set(provenance["split_gaps"]) == {"B"}
    run = research.run(experiment)
    assert research.reopen(run.id).payload["provenance"]["split_gaps"] == provenance["split_gaps"]


def test_reimporting_the_same_pdf_keeps_its_metadata_and_notes(service, tmp_path):
    research, _, _ = service
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4\n% same bytes")
    first = research.save_paper({"title": "Original title", "notes": "Keep my notes"}, str(path))
    second = research.save_paper({"title": "File title"}, str(path))
    assert first["id"] == second["id"] and second["notes"] == "Keep my notes"
    assert sum(p.get("pdf") == first["pdf"] for p in research.papers()) == 1


def test_canon_papers_merge_your_fields_and_keep_the_canon_metadata(service, tmp_path):
    research, store, _ = service
    # A row from before the canon (the D42 seed) with stale metadata and your notes.
    store.save_paper({"id": "sharpe1964", "title": "Old seed title", "year": "1964", "topic": "CAPM",
                      "status": "Read", "notes": "Beta is a covariance", "pdf": "", "run_ids": ["r1"]})
    research.save_paper({"title": "My own note on carry"})
    papers = {p["id"]: p for p in research.papers()}
    sharpe = papers["sharpe1964"]
    assert sharpe["title"].startswith("Capital Asset Prices")       # the canon's
    assert sharpe["status"] == "Read" and sharpe["notes"] == "Beta is a covariance"
    assert sharpe["run_ids"] == ["r1"] and sharpe["canon"] and sharpe["strand"] == "asset-pricing"
    assert sharpe["url"] == "https://doi.org/10.1111/j.1540-6261.1964.tb02865.x"
    assert papers["bachelier1900"]["status"] == "To read" and papers["bachelier1900"]["canon"]
    mine = next(p for p in papers.values() if p["title"] == "My own note on carry")
    assert not mine.get("canon")
    # Saving a merged canon paper stores your fields; display keys never reach the store.
    research.save_paper({**papers["kelly1956"], "status": "Reading"})
    stored = next(p for p in store.papers() if p["id"] == "kelly1956")
    assert stored["status"] == "Reading" and not {"canon", "strand", "label", "pdf_available"} & set(stored)


def test_a_broken_canon_leaves_your_papers_and_says_why(tmp_path, monkeypatch):
    import quant_hub.services.research as research_module
    from quant_hub.data.literature import CanonError

    def broken():
        raise CanonError(["x1990: links to missing paper 'y'"])
    monkeypatch.setattr(research_module, "load_canon", broken)
    db = tmp_path / "r.db"
    prepare_store(db)
    store = ResearchStore(db)
    research = ResearchService(MarketService([SyntheticSource()]), store)
    research.save_paper({"title": "Still here"})
    assert [p["title"] for p in research.papers()] == ["Still here"]
    assert "missing paper" in research.canon_error
    store.close()
