"""Research orchestration: exact snapshots, costs, matched tests and papers.

All methods that touch data or disk run on TaskRunner from the GUI. Price
panels use the intersection of observed sessions, never forward fills.
The benchmark is an equal-weight *buy-and-hold* basket, entered at the
same first test close and charged the same transaction cost rate. Its
exposure is 100%, independent of the strategy's risk target.

Sweeps compare a common test window. DSR is conditional on the owner's
explicit effective independent-trial count; all grid trials are retained.
The test window has been used for selection: it is not an untouched final
holdout. Both Sharpe SE and DSR use IID assumptions, as labelled in R1.

Saved arrays replay the actual decisions without refitting or fetching.
Source SHA-256 records the model implementation; changed code is reported
on reopen. Bitwise replay across NumPy/platform versions is not promised.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

import numpy as np
import pandas as pd

from quant_hub import __version__
from quant_hub.data.types import PriceBasis
from quant_hub.models.backtest import BacktestConfig, BacktestResult, backtest
from quant_hub.models.backtest_stats import performance_summary, deflated_sharpe
from quant_hub.models.research_worlds import WORLDS, research_world
from quant_hub.models.strategies import StrategySpec, strategy_targets
from quant_hub.services.market_service import MarketService
from quant_hub.storage.research_store import ResearchStore

WORLD_SOURCE = "Controlled world"
_FORMAT_VERSION = 1


@dataclass(frozen=True)
class Experiment:
    strategy: StrategySpec = StrategySpec()
    costs: BacktestConfig = BacktestConfig(half_spread_bps=1, commission_bps=1)
    source: str = WORLD_SOURCE
    symbols: tuple[str, ...] = ("WORLD",)
    basis: str = "adjusted"
    start: str = "2020-01-01"
    end: str = "2026-10-19"
    world: str = WORLDS[0]
    observations: int = 1000
    hypothesis: str = ""

    def __post_init__(self):
        if not self.symbols or len(set(self.symbols)) != len(self.symbols) or any(not s.strip() for s in self.symbols):
            raise ValueError("universe must contain unique nonempty symbols")
        PriceBasis(self.basis)
        if datetime.fromisoformat(self.start) >= datetime.fromisoformat(self.end):
            raise ValueError("start date must precede end date")
        if self.costs.periods_per_year != 252 or self.costs.allow_short:
            raise ValueError("Research v1 uses 252-session annualization and long/cash rules")
        if self.strategy.gross_cap > self.costs.max_gross_exposure:
            raise ValueError("strategy exposure cap exceeds the accounting limit")


@dataclass(frozen=True)
class ResearchRun:
    id: str
    payload: dict
    result: BacktestResult
    benchmark: BacktestResult
    notes: str = ""
    code_changed: bool = False


def _frame(frame: pd.DataFrame) -> dict:
    return {"dates": [d.isoformat() for d in frame.index], "symbols": list(frame.columns),
            "values": frame.to_numpy().tolist()}


def _unframe(data: dict) -> pd.DataFrame:
    return pd.DataFrame(data["values"], index=pd.DatetimeIndex(data["dates"]), columns=data["symbols"])


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _finite(value):
    """Portable JSON: undefined statistics are null, never nonstandard NaN."""
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    files = sorted(root.rglob("*.py"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class ResearchService:
    def __init__(self, market: MarketService, store: ResearchStore):
        self._market, self._store = market, store

    def prices(self, experiment: Experiment) -> tuple[pd.DataFrame, dict]:
        if experiment.source == WORLD_SOURCE:
            prices = research_world(experiment.world, experiment.observations,
                                    experiment.strategy.seed, experiment.symbols)
            return prices, {"source": WORLD_SOURCE, "world": experiment.world,
                            "seed": experiment.strategy.seed, "basis": "synthetic price",
                            "sessions": len(prices), "calendar": "synthetic business days"}
        histories = [self._market.get_history(s, datetime.fromisoformat(experiment.start),
                     datetime.fromisoformat(experiment.end), experiment.source,
                     basis=PriceBasis(experiment.basis)) for s in experiment.symbols]
        prices = pd.concat([h.frame["close"].rename(s) for h, s in zip(histories, experiment.symbols)], axis=1)
        original = len(prices)
        prices = prices.dropna().sort_index()
        return prices, {"source": experiment.source, "basis": experiment.basis,
                        "sessions": len(prices), "excluded_unmatched_sessions": original - len(prices),
                        "calendar": "intersection of observed daily sessions"}

    def run(self, experiment: Experiment, *, prices: pd.DataFrame | None = None,
            provenance: dict | None = None, first_decision: int | None = None) -> ResearchRun:
        if prices is None:
            prices, provenance = self.prices(experiment)
        signals = strategy_targets(prices, experiment.strategy)
        first = max(signals.first_decision, first_decision or 0)
        test_prices, targets = prices.iloc[first:], signals.targets.iloc[first:]
        result = backtest(test_prices, targets, experiment.costs)
        benchmark_targets = self._benchmark_targets(test_prices, experiment.costs)
        benchmark = backtest(test_prices, benchmark_targets, experiment.costs)
        summary = performance_summary(result.net_returns, cash_rate=experiment.costs.cash_rate,
                                      turnover=result.turnover)
        run_id = uuid4().hex
        payload = {"format": _FORMAT_VERSION, "id": run_id,
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "title": f"{experiment.strategy.name} · {', '.join(experiment.symbols)}",
                   "experiment": asdict(experiment), "provenance": provenance or {},
                   "software": {"version": __version__, "source_sha256": source_fingerprint(),
                                "numpy": np.__version__, "pandas": pd.__version__},
                   "prices": _frame(prices), "first_decision": first,
                   "targets": _frame(targets), "benchmark_targets": _frame(benchmark_targets),
                   "fits": signals.fits, "summary": asdict(summary),
                   "equity": result.equity.tolist(), "benchmark_equity": benchmark.equity.tolist(),
                   "fees": result.fees.tolist(), "net_returns": result.net_returns.tolist()}
        payload = _finite(payload)
        payload["evidence_sha256"] = _digest(payload)
        self._store.save_run(run_id, payload["created_at"], payload["title"], payload)
        return ResearchRun(run_id, payload, result, benchmark)

    @staticmethod
    def _benchmark_targets(prices, costs):
        # Initial equal notionals, then let shares drift. Uniform entry
        # costs scale every holding equally, so these weights create no
        # subsequent rebalance trades (up to floating-point roundoff).
        relative = prices.iloc[:-1] / prices.iloc[0]
        return relative.div(relative.sum(axis=1), axis=0)

    def sweep(self, experiment: Experiment, lookbacks: list[int], thresholds: list[float],
              independent_trials: int) -> dict:
        if not 2 <= len(lookbacks) <= 5 or not 2 <= len(thresholds) <= 5:
            raise ValueError("choose a 2×2 to 5×5 grid, at most 25 trials")
        if len(set(lookbacks)) != len(lookbacks) or len(set(thresholds)) != len(thresholds):
            raise ValueError("sweep values must be unique")
        if experiment.strategy.name == "GARCH volatility":
            raise ValueError("GARCH has no threshold; use individual volatility-target experiments")
        count = len(lookbacks) * len(thresholds)
        if not isinstance(independent_trials, int) or not 1 <= independent_trials <= count:
            raise ValueError("effective independent trials must be between 1 and grid size")
        # Lookback is the risk window for model rules, and signal/risk window
        # for the two baselines. Keep training fixed, with one common OOS start.
        specs = [replace(experiment.strategy, lookback=l, threshold=h) for h in thresholds for l in lookbacks]
        prices, provenance = self.prices(experiment)
        runs = [self.run(replace(experiment, strategy=s), prices=prices, provenance=provenance)
                for s in specs]
        sharpes = np.array([r.payload["summary"]["sharpe"]["sharpe"]
                            if r.payload["summary"]["sharpe"]["sharpe"] is not None else np.nan for r in runs])
        finite = np.isfinite(sharpes)
        best = runs[int(np.nanargmax(sharpes))] if finite.any() else runs[0]
        probability, reason = None, ""
        try:
            dsr = deflated_sharpe(best.result.net_returns, trial_sharpes=sharpes,
                                 independent_trials=independent_trials,
                                 cash_rate=experiment.costs.cash_rate)
            probability = _finite(dsr.probability)
            if probability is None:
                reason = "Undefined Sharpe uncertainty"
        except ValueError as exc:
            reason = str(exc)
        sweep = {"lookbacks": lookbacks, "thresholds": thresholds,
                 "sharpes": _finite(sharpes.tolist()), "run_ids": [r.id for r in runs],
                 "independent_trials": independent_trials, "deflated_probability_iid": probability,
                 "reason": reason, "selected_run": best.id,
                 "selection_warning": "This test window was used to select the best trial; it is not an untouched holdout."}
        # The grid manifest is an immutable run too, tied to the exact data
        # and each constituent experiment. Never retain only its winner.
        manifest = dict(best.payload)
        manifest.update(id=uuid4().hex, title="Sweep · " + best.payload["title"], sweep=sweep)
        manifest.pop("evidence_sha256")
        manifest["evidence_sha256"] = _digest(manifest)
        self._store.save_run(manifest["id"], manifest["created_at"], manifest["title"], manifest)
        return {"best": replace(best, id=manifest["id"], payload=manifest), "sweep": sweep}

    def list_runs(self):
        return self._store.list_runs()

    def reopen(self, run_id: str) -> ResearchRun:
        payload, notes = self._store.load_run(run_id)
        if payload.get("format") != _FORMAT_VERSION:
            raise ValueError("unsupported saved research format")
        check = dict(payload)
        expected = check.pop("evidence_sha256")
        if _digest(check) != expected:
            raise ValueError("saved evidence fingerprint does not match")
        prices = _unframe(payload["prices"]).iloc[payload["first_decision"]:]
        config = BacktestConfig(**payload["experiment"]["costs"])
        result = backtest(prices, _unframe(payload["targets"]), config)
        benchmark = backtest(prices, _unframe(payload["benchmark_targets"]), config)
        if not np.allclose(result.equity, payload["equity"], rtol=1e-11, atol=1e-8):
            raise ValueError("saved accounting no longer replays within tolerance")
        return ResearchRun(run_id, payload, result, benchmark, notes,
                           payload["software"]["source_sha256"] != source_fingerprint())

    def save_notes(self, run_id: str, notes: str):
        self._store.save_notes(run_id, notes)

    def papers(self):
        existing = self._store.papers()
        ids = {p["id"] for p in existing}
        for paper in _FOUNDATIONS:
            if paper["id"] not in ids:
                self.save_paper(paper)
        papers = self._store.papers()
        for paper in papers:
            paper["pdf_available"] = bool(paper.get("pdf") and self._store.library_path
                and (self._store.library_path / Path(paper["pdf"]).name).is_file())
        return sorted(papers, key=lambda p: (p.get("year", ""), p["title"]))

    def save_paper(self, metadata: dict, pdf_path: str | None = None):
        paper = {"id": uuid4().hex, "authors": "", "year": "", "topic": "",
                 "status": "To read", "notes": "", "url": "", "pdf": "", "run_ids": [], **metadata}
        paper.pop("pdf_available", None)
        if not paper.get("title", "").strip():
            raise ValueError("paper title is required")
        if pdf_path:
            if self._store.library_path is None:
                raise ValueError("PDF imports require a file-backed research store")
            path = Path(pdf_path)
            with path.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise ValueError("the selected file is not a PDF")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if "id" not in metadata:
                duplicate = next((p for p in self._store.papers() if p.get("pdf") == digest + ".pdf"), None)
                if duplicate is not None:
                    paper = duplicate
            self._store.library_path.mkdir(parents=True, exist_ok=True)
            target = self._store.library_path / (digest + ".pdf")
            if not target.exists():
                shutil.copyfile(path, target)
            paper["pdf"] = target.name
        self._store.save_paper(paper)
        return paper

    def paper_path(self, paper: dict) -> str:
        if not paper.get("pdf") or self._store.library_path is None:
            raise ValueError("this is a reference; attach a local PDF to read it here")
        path = (self._store.library_path / paper["pdf"]).resolve()
        if path.parent != self._store.library_path.resolve() or not path.is_file():
            raise ValueError("local PDF is missing; attach it again to relink")
        return str(path)


_FOUNDATIONS = [
    {"id": "bachelier1900", "title": "Théorie de la spéculation", "authors": "Louis Bachelier",
     "year": "1900", "topic": "Stochastic prices", "url": "https://archive.numdam.org/articles/10.24033/asens.476/"},
    {"id": "sharpe1964", "title": "Capital Asset Prices: A Theory of Market Equilibrium under Conditions of Risk",
     "authors": "William F. Sharpe", "year": "1964", "topic": "CAPM",
     "url": "https://doi.org/10.1111/j.1540-6261.1964.tb02865.x"},
    {"id": "famafrench1993", "title": "Common risk factors in the returns on stocks and bonds",
     "authors": "Eugene F. Fama; Kenneth R. French", "year": "1993", "topic": "Factors",
     "url": "https://doi.org/10.1016/0304-405X(93)90023-5"},
    {"id": "dupire1994", "title": "Pricing with a smile", "authors": "Bruno Dupire",
     "year": "1994", "topic": "Local volatility",
     "url": "https://www.risk.net/sites/default/files/import_unmanaged/risk.net/data/risk/pdf/technical/2007/risk20_0707_technical_volatility.pdf"},
    {"id": "gatheraljacquier2012", "title": "Arbitrage-free SVI volatility surfaces",
     "authors": "Jim Gatheral; Antoine Jacquier", "year": "2012 preprint", "topic": "SVI / arbitrage",
     "url": "https://arxiv.org/abs/1204.0646"},
]
