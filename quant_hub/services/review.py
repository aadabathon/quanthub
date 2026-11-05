"""The daily review: the owner's rules against today's book (W7).

One run is the plan's job chain, on the task-worker thread:

1. **Rules.** Read the owner's ``REGIME.md`` (``[regime] path``) and store
   its text as a version when it's new (``storage/regime_store.py``). A
   value it can't read stops the run and names the line.
2. **Positions.** The newest export in ``[positions] folder``, by the date
   printed inside each file, so names don't matter. Files that aren't
   positions are skipped and named. Without a folder, the newest snapshot
   imported on the Portfolio page. The export before it gives the
   journal's trade drafts: each symbol whose quantity changed.
3. **History, risk, regressions, growth.** ``PortfolioService.analyse``
   over section 2's source, benchmark, lookback and rate, with growth by
   the owner's sleeves (section 3). Missing history is fetched through the
   source, never by connecting to a broker on the owner's behalf.
4. **Days that decide the numbers** (``models/portfolio.py``, D53): the
   days that outweigh the rest of the mix's series or of a holding's own,
   read from the analysis's report, so they're the days the Portfolio
   page flags. They're removed in a second analysis. Section 4's choice
   says which analysis grades the return-based numbers; the review keeps
   both.
5. **Market state.** Section 7's gates on the benchmark's log returns:
   the HMM's filtered calm probability and its odds ahead, and GARCH's
   average volatility forecast.
6. **Valuations of holdings** (section 8): the owner's newest saved
   valuation of each holding and universe name, re-run on today's filings
   and today's price. A name with none is reported, not guessed: the
   assumptions are the owner's to set on the Valuation page.
7. **The checklist.** Every rule against its measure, over what it governs:
   the portfolio, each holding, each option, each stream, each pair of
   streams, each company. Section 1's persistence turns each stream's
   breaches into a thesis verdict. A1 makes it core-aware:
   - A core holding is exempt from max holding weight and max risk share.
     Its numbers are still shown: a core carries market risk by design.
   - Section 3's sleeve totals are graded like a holding's intended size.
   - The book's β is split into the core's and the rest's
     (``models/allocation.py``), with the core weight that would bring it
     into the Beta band. Shown, never applied.
   - Effective holdings look through the core (part 2, D66): each core
     fund's newest N-PORT report, its lines matched to the stocks held
     directly by ISIN (``services/security_ids.py``). Without a stored
     report the rule has no data, and says how to get one.
   - **Crash stress** (A2, D67): the book's β, with its Newey–West
     error band, replayed through Ken French's daily market from 1926
     (``models/stress.py``). Measured, never graded.
8. **The journal.** The run is stored whole. Notes and hand-placed trades
   are added after.

Only measures a rule reads are computed, so a blank section costs nothing.
Decision support only: nothing here places an order (D12).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quant_hub.data.holdings import FundHoldings, valid_isin
from quant_hub.data.positions import OPTION_MULTIPLIER, Holding, PositionSnapshot
from quant_hub.data.regime import Grade, Measure, Rule, RuleSet
from quant_hub.importers.positions_csv import read_positions_csv, read_positions_files
from quant_hub.importers.regime_md import parse_regime
from quant_hub.models.allocation import CoreNetting, core_netting, look_through
from quant_hub.models.dcf import (
    ValuationError,
    calibration,
    implied_growth,
    market_comparison,
    value,
)
from quant_hub.models.portfolio import TRADING_DAYS, InfluentialDay
from quant_hub.models.regime import (
    CalmGate,
    Check,
    Outcome,
    calm_regime,
    check,
    implied_growth_check,
    persistence,
    run_length,
    volatility_forecast,
)
from quant_hub.models.regression import NEWEY_WEST, regress_returns, residual_correlation
from quant_hub.models.stress import CrashStress, crash_stress
from quant_hub.providers.sec_edgar import SecError
from quant_hub.providers.sec_nport import NportError
from quant_hub.services.fund_holdings import FundHoldingsService
from quant_hub.services.external_data import ExternalDataService
from quant_hub.services.portfolio import PORTFOLIO_ROW, PortfolioService, PortfolioView
from quant_hub.services.security_ids import SecurityIdService
from quant_hub.services.valuation import ValuationService, refusal
from quant_hub.storage.bar_store import StorageError
from quant_hub.storage.regime_store import RegimeStore, ReviewRecord, TradeRecord

DEFAULT_BENCHMARK = "SPY"       # REGIME.md section 2: "SPY by default"
DEFAULT_LOOKBACK = 730          # section 2: "730 by default"
PORTFOLIO = "portfolio"
NO_ERROR = "no standard error yet"
CRASH_MARKET = ("french", "ff3.mkt_rf")      # the market's daily excess return, from 1926
CRASH_BILLS = ("french", "ff3.rf")           # the T-bill's daily return
THESIS_RULES = ("6.hurdle", "6.correlation", "6.stream-beta")
"""Section 1's tests: the growth hurdle, orthogonality and beta creep."""
CORE_EXEMPT = ("4.max-weight", "4.max-risk-share")
"""The single-name rules a core holding is shown under, not graded (A1)."""
_PAIR = " × "


class ReviewError(Exception):
    """The review can't run; the message says what to fix."""


@dataclass(frozen=True)
class ItemCheck:
    """One rule on one thing it governs (a holding, a stream, a pair...)."""

    item: str
    check: Check


@dataclass(frozen=True)
class StreamVerdict:
    """Section 1 for one stream: today's breaches and how long they've run."""

    sleeve: str
    breaches: tuple[str, ...]       # the thesis rules it breached today
    run: int                        # reviews in a row with a breach, today's included
    grade: Grade | None


@dataclass(frozen=True)
class TradeDraft:
    """A symbol whose quantity changed between two exports."""

    symbol: str
    before: float
    after: float
    price: float | None

    @property
    def change(self) -> float:
        return self.after - self.before


@dataclass(frozen=True, eq=False)
class Review:
    run_at: datetime
    review_id: int
    version_id: int
    rules: RuleSet
    snapshot: PositionSnapshot
    files: tuple[str, ...]              # the export(s) read; empty for a stored snapshot
    skipped: tuple[str, ...]            # files in the folder that aren't positions, and why
    view: PortfolioView                 # as measured
    graded: PortfolioView               # the one the checks read (section 4's choice)
    influential: tuple[InfluentialDay, ...]   # the days that decide ``view``'s numbers
    removed_days: tuple[date, ...]      # the flagged days, removed from ``graded`` if chosen
    checks: tuple[ItemCheck, ...]
    streams: tuple[StreamVerdict, ...]
    calm: CalmGate | None
    volatility: float | None
    drafts: tuple[TradeDraft, ...]
    notes: tuple[str, ...]
    netting: CoreNetting | None = None  # the core's β and the rest's (A1)
    netting_note: str = ""              # why there's none, when section 3 has a core
    crash: CrashStress | None = None    # the book's β through every stored crash (A2)
    crash_note: str = ""                # why there's none

    def breaches(self, consequence=None) -> list[ItemCheck]:
        """Breached checks, optionally of one grade or effect."""
        return [c for c in self.checks if c.check.outcome is Outcome.BREACH
                and (consequence is None or c.check.consequence is consequence)]


@dataclass(frozen=True, eq=False)
class Graded:
    """Sections 3 and 4 on any book, as measured (A3's What-if): the
    checks, A1's netting, A2's crash stress, and the look-through's notes."""

    checks: tuple[ItemCheck, ...]
    effective: float | None                 # N_eff, looked through where there's a core
    netting: CoreNetting | None
    netting_note: str
    crash: CrashStress | None
    crash_note: str
    notes: tuple[str, ...]
    effective_note: str = ""                # why N_eff couldn't look through the core

    def count(self, grade: Grade) -> int:
        return sum(c.check.outcome is Outcome.BREACH and c.check.consequence is grade
                   for c in self.checks)


class ReviewService:
    def __init__(self, portfolio: PortfolioService, valuation: ValuationService,
                 store: RegimeStore, regime_path: Path, positions_folder: Path | None = None,
                 now=datetime.now, fund_holdings: FundHoldingsService | None = None,
                 security_ids: SecurityIdService | None = None,
                 external_data: ExternalDataService | None = None) -> None:
        self._portfolio = portfolio
        self._valuation = valuation
        self._funds = fund_holdings
        self._ids = security_ids
        self._external = external_data
        self._store = store
        self._regime_path = Path(regime_path)
        self._folder = Path(positions_folder) if positions_folder else None
        self._now = now

    @property
    def regime_path(self) -> Path:
        return self._regime_path

    @property
    def positions_folder(self) -> Path | None:
        return self._folder

    # ------------------------------------------------------------- the chain
    def load_rules(self) -> tuple[RuleSet, int, bool]:
        """The owner's rules, their stored version, and whether it's new."""
        if not self._regime_path.exists():
            raise ReviewError(f"no rules at {self._regime_path}: copy the repository's "
                              "REGIME.md there and fill it in")
        text = self._regime_path.read_text(encoding="utf-8")
        rules = parse_regime(text)
        version_id, new = self._store.store_version(
            text, rules.version, rules.written.isoformat() if rules.written else "",
            rules.reason, self._now())
        return rules, version_id, new

    def exports(self) -> tuple[dict[date, list[Path]], tuple[str, ...]]:
        """The positions folder's exports by their as-of day, and the files
        that aren't positions (with why)."""
        if self._folder is None:
            return {}, ()
        if not self._folder.is_dir():
            raise ReviewError(f"the positions folder {self._folder} doesn't exist")
        dated: dict[date, list[Path]] = {}
        skipped = []
        for path in sorted(self._folder.glob("*.csv")):
            try:
                day = read_positions_csv(path).as_of.date()
            except Exception as exc:        # a file that isn't positions is named, not fatal
                skipped.append(f"{path.name}: {exc}")
                continue
            dated.setdefault(day, []).append(path)
        return dated, tuple(skipped)

    def run(self, ticks: Mapping[str, bool] | None = None) -> Review:
        """The whole chain (module docstring). ``ticks`` are the owner's own
        indicators, ticked by hand."""
        run_at = self._now()
        rules, version_id, _ = self.load_rules()
        settings = rules.settings
        source = settings.history_source or _blank("History source")
        if settings.cash_rate is None:
            _blank("Cash rate")
        benchmark = settings.benchmark or DEFAULT_BENCHMARK
        lookback = settings.lookback_days or DEFAULT_LOOKBACK
        rate = settings.cash_rate
        notes: list[str] = []

        snapshot, previous, files, skipped = self._positions()
        names = {row.symbol: row.sleeve for row in rules.sleeves if row.sleeve}
        unassigned = sorted({p.option.underlying if p.option else p.symbol
                             for p in snapshot.positions if p.kind is not Holding.CASH}
                            - set(names))
        if unassigned:
            notes.append(f"No sleeve yet in section 3, so each is its own: {', '.join(unassigned)}.")

        def analyse(exclude: Sequence[date] = ()) -> PortfolioView:
            return self._portfolio.analyse(snapshot, None, source, benchmark, lookback, rate,
                                           now=run_at, sleeves=names, exclude=exclude)

        view = analyse()
        notes.extend(view.left_out)
        # The same days as the Portfolio page's flags and its WITHOUT (D53).
        influential = view.report.influence.flagged if view.report.influence else ()
        removed = tuple(sorted({d.date.date() for d in influential}))
        graded = view
        if removed and rules.without_dominant_day:
            graded = analyse(removed)
        if removed:
            days = ", ".join(d.isoformat() for d in removed)
            one = len(removed) == 1
            notes.append(f"{'A day decides' if one else 'Days decide'} the numbers ({days}); "
                         "the checks read "
                         + (f"the numbers without {'it' if one else 'them'}."
                            if graded is not view else "the numbers as measured (section 4)."))

        wanted = {rule.measure for rule in rules.rules}
        measures, missing = _book_measures(graded, snapshot, wanted)
        if "effective holdings" in wanted and rules.core & {row.symbol for row in graded.rows}:
            effective, reason, look_notes = self._look_through(graded, rules)
            measures.pop("effective holdings", None)
            if effective is None:
                missing["effective holdings"] = reason
            else:
                measures["effective holdings"] = [(PORTFOLIO, effective, "")]
            notes.extend(look_notes)
        sleeves = _sleeve_measures(graded, rules, benchmark, rate, wanted)
        measures.update(sleeves[0])
        missing.update(sleeves[1])
        calm, volatility = None, None
        if wanted & {"calm probability", "calm odds", "volatility forecast"}:
            calm, volatility, gate_measures, gate_missing = _gates(graded, rules, benchmark,
                                                                  wanted)
            measures.update(gate_measures)
            missing.update(gate_missing)
        for rule in rules.rules:
            if rule.measure.startswith("indicator: "):
                name = rule.measure.removeprefix("indicator: ")
                measures[rule.measure] = [(name, Measure.exact(1.0 if (ticks or {}).get(name)
                                                               else 0.0), "")]
        implied: dict[str, tuple[tuple[float, ...], float] | str] = {}
        if any(rule.key.startswith("8.") for rule in rules.rules):
            tickers = sorted({p.symbol for p in snapshot.positions if p.kind is Holding.EQUITY}
                             | set(rules.universe.tickers))
            company, implied = self._valuations(tickers, source)
            measures.update(company)

        checks = _checks(rules, measures, missing, implied, graded)
        streams = self._streams(rules, checks, run_at)
        drafts = _drafts(previous, snapshot) if previous is not None else ()
        netting, netting_note = _netting(graded, rules)
        crash, crash_note = self._crash(graded)
        payload = _payload(rules, snapshot, files, checks, streams, influential, removed, calm,
                           volatility, view, netting, crash)
        review_id = self._store.save_review(run_at, version_id, snapshot.as_of.date().isoformat(),
                                            payload)
        return Review(run_at, review_id, version_id, rules, snapshot,
                      tuple(p.name for p in files), skipped, view, graded, influential, removed,
                      tuple(checks), tuple(streams), calm, volatility, tuple(drafts),
                      tuple(notes), netting, netting_note, crash, crash_note)

    def current_view(self, now: datetime | None = None) -> tuple[PortfolioView, float]:
        """Today's book as the review analyses it, as measured, and the cash
        rate: the Hedge Lab's "today's mix" (HEDGE U4). Needs section 2's
        history source and cash rate, and positions."""
        rules, _, _ = self.load_rules()
        settings = rules.settings
        source = settings.history_source or _blank("History source")
        if settings.cash_rate is None:
            _blank("Cash rate")
        snapshot, _, _, _ = self._positions()
        names = {row.symbol: row.sleeve for row in rules.sleeves if row.sleeve}
        view = self._portfolio.analyse(snapshot, None, source,
                                       settings.benchmark or DEFAULT_BENCHMARK,
                                       settings.lookback_days or DEFAULT_LOOKBACK,
                                       settings.cash_rate, now=now or self._now(), sleeves=names)
        return view, settings.cash_rate

    def grade(self, view: PortfolioView, snapshot: PositionSnapshot, rules: RuleSet) -> Graded:
        """Sections 3 and 4 on ``view``, as measured, with A1's core
        exemption and look-through, A1's netting and A2's crash stress: the
        What-if page grades every vector as the review grades the book
        (ALLOCATION A3). Sections 5–8 read options, streams, gates and
        valuations, which a vector doesn't have."""
        book_rules = replace(rules, rules=tuple(r for r in rules.rules if r.key.startswith("4.")))
        wanted = {rule.measure for rule in book_rules.rules}
        measures, missing = _book_measures(view, snapshot, wanted | {"effective holdings"})
        notes: list[str] = []
        effective, effective_note = view.report.n_effective, ""
        if rules.core & {row.symbol for row in view.rows}:
            looked, reason, notes = self._look_through(view, rules)
            measures.pop("effective holdings", None)
            if looked is None:
                missing["effective holdings"] = reason
                effective, effective_note = None, reason
            else:
                measures["effective holdings"] = [(PORTFOLIO, looked, "")]
                effective = looked.estimate
        checks = _checks(book_rules, {k: v for k, v in measures.items() if k in wanted},
                         missing, {}, view)
        netting, netting_note = _netting(view, rules)
        crash, crash_note = self._crash(view)
        return Graded(tuple(checks), effective, netting, netting_note, crash, crash_note,
                      tuple(notes), effective_note)

    def latest_beta(self) -> float | None:
        """The book's β from the newest review that measured one (A2's)."""
        for record in self._store.reviews(5):
            crash = record.payload.get("crash")
            if crash and crash.get("beta") is not None:
                return float(crash["beta"])
        return None

    # ------------------------------------------------------------ the journal
    def reviews(self, limit: int | None = None) -> list[ReviewRecord]:
        return self._store.reviews(limit)

    def save_notes(self, review_id: int, notes: str) -> None:
        self._store.save_notes(review_id, notes)

    def add_trade(self, review_id: int | None, symbol: str, quantity: float,
                  price: float | None, reason: str, rule: str = "") -> int:
        """A trade the owner placed by hand, against the review that prompted
        it. Recorded only: nothing is sent anywhere (D12)."""
        if not symbol.strip() or not reason.strip():
            raise ValueError("a trade needs a symbol and a reason")
        if not math.isfinite(quantity) or quantity == 0:
            raise ValueError("a trade's quantity must be a nonzero number")
        if price is not None and not (math.isfinite(price) and price > 0):
            raise ValueError("a price must be positive")
        return self._store.add_trade(review_id, self._now(), symbol.strip().upper(), quantity,
                                     price, reason.strip(), rule)

    def trades(self, review_id: int | None = None) -> list[TradeRecord]:
        return self._store.trades(review_id)

    # ---------------------------------------------------------------- helpers
    def _positions(self) -> tuple[PositionSnapshot, PositionSnapshot | None,
                                  tuple[Path, ...], tuple[str, ...]]:
        """Today's snapshot, the one before it (for trade drafts), the files
        read, and the files skipped."""
        if self._folder is not None:
            dated, skipped = self.exports()
            if not dated:
                raise ReviewError(f"no positions export in {self._folder}"
                                  + (f" ({len(skipped)} files aren't positions)" if skipped
                                     else ""))
            days = sorted(dated)
            files = tuple(dated[days[-1]])
            try:
                snapshot = read_positions_files(list(files))
                previous = read_positions_files(list(dated[days[-2]])) if len(days) > 1 else None
            except ValueError as exc:
                raise ReviewError(f"the newest exports can't be read together: {exc}") from exc
            return snapshot, previous, files, skipped
        stored = self._portfolio.snapshots()
        if not stored:
            raise ReviewError("no positions: set [positions] folder in settings.toml, or import "
                              "an export on the Portfolio page")
        snapshot = self._portfolio.load(stored[0].id)
        previous = self._portfolio.load(stored[1].id) if len(stored) > 1 else None
        return snapshot, previous, (), ()

    def _valuations(self, tickers: Sequence[str], source: str
                    ) -> tuple[dict, dict[str, tuple[tuple[float, ...], float] | str]]:
        """Section 8's measures for each name, from the owner's newest saved
        valuation of it, re-run on today's filings and price."""
        newest = {}
        for record in self._valuation.saved():          # newest first
            newest.setdefault(record.ticker.upper(), record)
        measures: dict[str, list] = {"margin of safety": [], "terminal share": [],
                                     "missing fields": []}
        implied: dict[str, tuple[tuple[float, ...], float] | str] = {}
        for ticker in tickers:
            record = newest.get(ticker)

            def none(reason: str, names=("margin of safety", "terminal share", "missing fields")):
                for name in names:
                    measures[name].append((ticker, None, reason))
                implied.setdefault(ticker, reason)

            if record is None:
                none("no saved valuation: value it on the Valuation page")
                continue
            _, assumptions, hist, note = self._valuation.reopen(record.id)
            if hist is None:
                none(note)
                continue
            try:
                result = value(hist, assumptions)
            except ValuationError as exc:
                none(f"the DCF refuses: {refusal(exc)}")
                continue
            measures["terminal share"].append((ticker, Measure.exact(result.terminal_share), ""))
            measures["missing fields"].append((ticker, Measure.exact(float(len(hist.missing))),
                                               ""))
            try:
                price = self._valuation.price(ticker, source).value
            except Exception as exc:
                none(f"no price: {exc}", names=("margin of safety",))
                continue
            comparison = market_comparison(result, price)
            measures["margin of safety"].append((ticker,
                                                 Measure.exact(comparison.margin_of_safety), ""))
            cagr = calibration(hist).revenue_cagr
            implied[ticker] = ((implied_growth(hist, assumptions, price), cagr)
                               if cagr is not None else "no trailing revenue CAGR in the filings")
        return measures, implied

    def _crash(self, view: PortfolioView) -> tuple[CrashStress | None, str]:
        """A2: the book's β (W2's, with its Newey–West error; the risk
        model's without one) through Ken French's daily market. None, with
        how to get it, when the series isn't stored."""
        if self._external is None:
            return None, "The review has no outside series to replay."
        try:
            market = self._external.series(*CRASH_MARKET).values
            bills = self._external.series(*CRASH_BILLS).values
        except LookupError as exc:
            return None, f"Crash stress needs Ken French's daily market: {exc}."
        if view.regressions is not None:
            beta, error = view.regressions.beta(PORTFOLIO_ROW)
        elif view.report.beta is not None:
            beta, error = view.report.beta, None
        else:
            return None, "No β to replay: the book has no benchmark history."
        try:
            return crash_stress(market, bills, beta, error), ""
        except ValueError as exc:
            return None, f"The crash replay failed: {exc}."

    def _look_through(self, view: PortfolioView, rules: RuleSet
                      ) -> tuple[Measure | None, str, list[str]]:
        """Effective holdings with the core looked through (part 2, D66):
        the measure, or None and why, and the run's notes on it."""
        held = {row.symbol: row.weight for row in view.rows}
        core = sorted(rules.core & set(held))
        if self._funds is None:
            return None, "the review has no fund holdings to look through", []
        reports: dict[str, FundHoldings] = {}
        for fund in core:
            try:
                reports[fund] = self._funds.holdings(fund)
            except (SecError, NportError, LookupError, StorageError) as exc:
                return None, (f"{fund}'s N-PORT holdings aren't stored, so the core can't be "
                              f"looked through: {exc}"), []
        direct = sorted(s for s in held if s not in rules.core)
        if self._ids is not None:
            isins, unmatched = self._ids.isins(direct)
        else:
            isins, unmatched = {}, {s: "no ISIN lookup" for s in direct}
        key_of = {s: isins.get(s) or f"ticker:{s}" for s in direct}
        weights: dict[str, float] = {}
        for s in direct:
            weights[key_of[s]] = weights.get(key_of[s], 0.0) + held[s]
        funds = {fund: (held[fund], [(_line_key(h, key_of), h.pct) for h in report.holdings
                                     if h.pct is not None])
                 for fund, report in reports.items()}
        result = look_through(weights, funds)
        notes = [f"Effective holdings look through {fund}'s N-PORT report as of "
                 f"{report.report_date} ({len(report.holdings)} lines, "
                 f"{report.weight_covered:.0%} of its net assets)." for fund, report in reports.items()]
        notes.append(f"Looked through: {result.n_effective:.1f}; counting each fund as one name: "
                     f"{view.report.n_effective:.1f}.")
        inside = [f"{s} (+{result.through_funds[key_of[s]]:.1%} of equity through it)"
                  for s in direct if key_of[s] in result.through_funds]
        if inside:
            notes.append(f"Held directly and inside the core: {', '.join(inside)}.")
        by_reason: dict[str, list[str]] = {}
        for symbol, reason in unmatched.items():
            by_reason.setdefault(reason, []).append(symbol)
        for reason, symbols in sorted(by_reason.items()):
            notes.append(f"No ISIN for {', '.join(symbols)} ({reason}), so "
                         f"{'it' if len(symbols) == 1 else 'they'} can't be matched to the "
                         "core's lines; with TWS connected, the next review looks "
                         f"{'it' if len(symbols) == 1 else 'them'} up.")
        return Measure.exact(result.n_effective, note=f"looked through {', '.join(core)}"), "", notes

    def _streams(self, rules: RuleSet, checks: Sequence[ItemCheck],
                 run_at: datetime) -> list[StreamVerdict]:
        """Section 1: each stream's breaches today, and the run they extend."""
        streams = sorted({row.sleeve for row in rules.sleeves
                          if row.sleeve and row.sleeve.startswith("stream: ")})
        if not streams:
            return []
        history = list(reversed(self._store.reviews()))           # oldest first
        verdicts = []
        for sleeve in streams:
            breaches = tuple(sorted({c.check.rule.key for c in checks
                                     if c.check.rule.key in THESIS_RULES
                                     and c.check.outcome is Outcome.BREACH
                                     and sleeve in c.item.split(_PAIR)}))
            past = [(record.run_at, bool(record.payload["streams"][sleeve]))
                    for record in history if sleeve in record.payload.get("streams", {})]
            run = run_length([*past, (run_at, bool(breaches))])
            if rules.thesis_runs is not None:
                grade = persistence(run, rules.thesis_runs)
            else:
                grade = Grade.WARN if run else None         # section 1: one failing review warns
            verdicts.append(StreamVerdict(sleeve, breaches, run, grade))
        return verdicts


# ---------------------------------------------------------------- measures

def _line_key(line, key_of: Mapping[str, str]) -> str:
    """A fund line's name for the look-through: its ISIN when it has a valid
    one; else a held symbol's key when its ticker is that symbol; else its
    ticker, CUSIP or name. Never matched by guesswork (``models/allocation``)."""
    if line.isin and valid_isin(line.isin.upper()):
        return line.isin.upper()
    ticker = line.ticker.strip().upper()
    if ticker:
        return key_of.get(ticker, f"ticker:{ticker}")
    if line.cusip.strip():
        return f"cusip:{line.cusip.strip().upper()}"
    return f"name:{line.name.strip()}"


def _blank(row: str):
    raise ReviewError(f"REGIME.md section 2: '{row}' is blank, and the review needs it")


def _book_measures(view: PortfolioView, snapshot: PositionSnapshot, wanted: set[str]
                   ) -> tuple[dict[str, list], dict[str, str]]:
    """Sections 4 and 5: the portfolio, each holding, each option."""
    r, equity = view.report, view.report.equity
    m: dict[str, list] = {
        "holding weight": [(row.symbol, Measure.exact(abs(row.weight)), "") for row in view.rows],
        "holding risk share": [(row.symbol, Measure.exact(row.risk_share, NO_ERROR), "")
                               for row in view.rows],
        "portfolio volatility": [(PORTFOLIO, Measure.exact(r.vol_annual, NO_ERROR), "")],
        "var 95": [(PORTFOLIO, Measure.exact(r.var_historical[0.95] / equity, NO_ERROR), "")],
        "es 99": [(PORTFOLIO, Measure.exact(r.es_historical[0.99] / equity, NO_ERROR), "")],
        "worst drawdown": [(PORTFOLIO, Measure.exact(-float(r.drawdown.min()), NO_ERROR), "")],
        "cash": [(PORTFOLIO, Measure.exact(view.cash / equity), "")],
        "effective holdings": [(PORTFOLIO, Measure.exact(r.n_effective), "")],
        "net delta": [(PORTFOLIO, Measure.exact(r.net_exposure), "")],
    }
    missing: dict[str, str] = {}
    if view.regressions is not None:
        beta, error = view.regressions.beta(PORTFOLIO_ROW)
        m["portfolio beta"] = [(PORTFOLIO, Measure.estimated(beta, error), "")]
    else:
        missing["portfolio beta"] = view.regression_note or "no regressions"
    if r.kelly > 0:
        fraction = r.net_exposure / r.kelly
        m["kelly fraction"] = [(PORTFOLIO, Measure.estimated(
            fraction, abs(r.net_exposure) * r.kelly_error / r.kelly ** 2), "")]
    else:
        missing["kelly fraction"] = "the estimated Kelly f* isn't positive, so there's no cap"
    options = [p for p in snapshot.positions if p.option is not None]
    m["premium at risk"] = [(PORTFOLIO, Measure.exact(
        sum(p.market_value or 0.0 for p in options if p.quantity > 0) / equity), "")]
    m["book vega"] = [(PORTFOLIO, Measure.exact(abs(view.book.vega) if view.book else 0.0), "")]
    m["short options"] = [(PORTFOLIO, Measure.exact(float(sum(p.quantity < 0 for p in options))),
                           "")]
    m["uncovered short options"] = [(PORTFOLIO, Measure.exact(float(_uncovered(snapshot,
                                                                               view.cash))), "")]
    m["days to expiry"] = [(p.symbol, Measure.exact(p.option.days_to_expiry(
        snapshot.as_of.date())), "") for p in options]
    return {k: v for k, v in m.items() if k in wanted}, missing


def _uncovered(snapshot: PositionSnapshot, cash: float) -> int:
    """Short options without cover: calls beyond the shares held, puts
    beyond the cash that secures them."""
    shares: dict[str, float] = {}
    for p in snapshot.positions:
        if p.kind is Holding.EQUITY:
            shares[p.symbol] = shares.get(p.symbol, 0.0) + p.quantity
    uncovered, secured = 0, cash
    for p in snapshot.positions:
        if p.option is None or p.quantity >= 0:
            continue
        need = -p.quantity * OPTION_MULTIPLIER
        if p.option.option_type == "call":
            held = shares.get(p.option.underlying, 0.0)
            if held >= need:
                shares[p.option.underlying] = held - need
            else:
                uncovered += 1
        else:
            if secured >= need * p.option.strike:
                secured -= need * p.option.strike
            else:
                uncovered += 1
    return uncovered


def _sleeve_measures(view: PortfolioView, rules: RuleSet, benchmark: str, rate: float,
                     wanted: set[str]) -> tuple[dict[str, list], dict[str, str]]:
    """Section 5's hedge cost and section 6: each stream's margin, size,
    beta, and residual correlations with the core and the other streams."""
    measures: dict[str, list] = {}
    missing: dict[str, str] = {}
    growth = view.growth
    labels = {row.sleeve for row in rules.sleeves if row.sleeve}
    streams = sorted(label for label in labels if label.startswith("stream: "))
    sleeve_rules = {"stream margin", "sleeve fraction of optimum", "stream beta",
                    "stream residual correlation", "hedge cost"} & wanted
    if not sleeve_rules:
        return measures, missing
    if growth is None:
        for name in sleeve_rules:
            missing[name] = view.growth_note or "no growth report"
        return measures, missing
    by_name = {s.name: s for s in growth.sleeves}
    if "hedge cost" in wanted:
        hedge = by_name.get("hedge")
        if hedge is None:
            measures["hedge cost"] = []
        else:
            measures["hedge cost"] = [("hedge", Measure.estimated(-hedge.excess_mean,
                                                                  hedge.excess_mean_error), "")]
    if not streams:
        for name in sleeve_rules - {"hedge cost"}:
            missing[name] = "no stream sleeves in section 3"
        return measures, missing
    present = [s for s in streams if s in by_name]
    measures["stream margin"] = [(s, Measure.estimated(by_name[s].margin,
                                                       by_name[s].excess_mean_error), "")
                                 for s in present]
    fractions = []
    for s in present:
        c = by_name[s]
        fractions.append((s, Measure.estimated(1.0 / c.c_star, c.c_star_error / c.c_star ** 2)
                          if c.c_star > 0 else None,
                          "" if c.c_star > 0 else "its growth-optimal size isn't positive"))
    measures["sleeve fraction of optimum"] = fractions
    if wanted & {"stream beta", "stream residual correlation"}:
        if view.returns is None or benchmark not in view.returns.columns:
            missing["stream beta"] = missing["stream residual correlation"] = (
                f"no {benchmark} history")
            return measures, missing
        series = _sleeve_returns(view, [*present, *(["core"] if "core" in by_name else [])],
                                 rate)
        try:
            fits = regress_returns(series, view.returns[[benchmark]], rate)
        except ValueError as exc:
            missing["stream beta"] = missing["stream residual correlation"] = (
                f"no sleeve regressions: {exc}")
            return measures, missing
        measures["stream beta"] = [(s, Measure.estimated(fit.value(benchmark),
                                                         fit.error(benchmark, NEWEY_WEST)), "")
                                   for s, fit in fits.items() if s.startswith("stream: ")]
        residuals = pd.DataFrame({name: fit.residuals for name, fit in fits.items()})
        corr, _, _ = residual_correlation(residuals)
        pairs = []
        names = list(residuals.columns)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if not (a.startswith("stream: ") or b.startswith("stream: ")):
                    continue
                rho = float(corr.loc[a, b])
                usable = math.isfinite(rho) and abs(rho) < 1
                pairs.append((f"{a}{_PAIR}{b}",
                              Measure.of_correlation(rho, len(residuals)) if usable else None,
                              "" if usable else "a residual with no variance"))
        measures["stream residual correlation"] = pairs
    return measures, missing


def _sleeve_returns(view: PortfolioView, sleeves: Sequence[str], rate: float) -> pd.DataFrame:
    """Each sleeve's own daily simple return: its excess over cash (W3's
    series, a fraction of equity) per unit of its value share, plus cash."""
    growth = view.growth
    shares = {s.name: s.value_share for s in growth.sleeves}
    cash = rate / TRADING_DAYS
    return pd.DataFrame({s: growth.excess[s] / shares[s] + cash for s in sleeves
                         if s in growth.excess.columns and shares.get(s, 0.0) > 0})


def _gates(view: PortfolioView, rules: RuleSet, benchmark: str, wanted: set[str]
           ) -> tuple[CalmGate | None, float | None, dict[str, list], dict[str, str]]:
    """Section 7 on the benchmark's daily log returns."""
    measures: dict[str, list] = {}
    missing: dict[str, str] = {}
    market = rules.market
    if view.returns is None or benchmark not in view.returns.columns:
        for name in ("calm probability", "calm odds", "volatility forecast"):
            missing[name] = f"no {benchmark} history"
        return None, None, measures, missing
    log_returns = np.log1p(view.returns[benchmark].to_numpy())
    calm = volatility = None
    if wanted & {"calm probability", "calm odds"}:
        if market.regimes is None:
            missing["calm probability"] = missing["calm odds"] = "K is blank in section 7"
        else:
            try:
                calm = calm_regime(log_returns, market.regimes,
                                   horizons=(market.odds_horizon or 1,))
                measures["calm probability"] = [(benchmark, Measure.exact(calm.probability), "")]
                if market.odds_horizon:
                    measures["calm odds"] = [(benchmark, Measure.exact(
                        calm.odds[market.odds_horizon]), "")]
            except ValueError as exc:
                missing["calm probability"] = missing["calm odds"] = f"the HMM fit failed: {exc}"
    if "volatility forecast" in wanted:
        if market.volatility_horizon is None:
            missing["volatility forecast"] = "the horizon in days is blank in section 7"
        else:
            try:
                volatility = volatility_forecast(log_returns, market.volatility_horizon)
                measures["volatility forecast"] = [(benchmark, Measure.exact(volatility), "")]
            except ValueError as exc:
                missing["volatility forecast"] = f"the GARCH fit failed: {exc}"
    return calm, volatility, measures, missing


def _checks(rules: RuleSet, measures: Mapping[str, list], missing: Mapping[str, str],
            implied: Mapping[str, tuple[tuple[float, ...], float] | str],
            view: PortfolioView) -> list[ItemCheck]:
    """Every rule over what it governs, then section 3's intended sizes."""
    out: list[ItemCheck] = []
    for rule in rules.rules:
        if rule.measure == "implied growth":
            if not implied:
                out.append(ItemCheck("—", Check(rule, None, Outcome.MISSING,
                                                reason="no names to value")))
            for ticker, entry in sorted(implied.items()):
                if isinstance(entry, str):
                    out.append(ItemCheck(ticker, Check(rule, None, Outcome.MISSING, reason=entry)))
                else:
                    out.append(ItemCheck(ticker, implied_growth_check(rule, *entry)))
            continue
        entries = measures.get(rule.measure)
        if entries is None:
            out.append(ItemCheck("—", Check(rule, None, Outcome.MISSING,
                                            reason=missing.get(rule.measure, "not measured"))))
        elif not entries:
            out.append(ItemCheck("—", Check(rule, None, Outcome.PASS,
                                            reason="nothing it governs is held")))
        else:
            exempt = rules.core if rule.key in CORE_EXEMPT else frozenset()
            for item, measure, reason in entries:
                if measure is None:
                    out.append(ItemCheck(item, Check(rule, None, Outcome.MISSING, reason=reason)))
                elif item in exempt:
                    out.append(ItemCheck(item, Check(rule, measure, Outcome.EXEMPT,
                                                     reason="the core")))
                else:
                    out.append(ItemCheck(item, check(rule, measure)))
    weights = {row.symbol: abs(row.weight) for row in view.rows}
    if rules.sleeve_consequence is not None:
        for row in rules.sleeves:
            if row.intended is None:
                continue
            rule = Rule(f"3.size.{row.symbol}", f"{row.symbol}: intended size", "holding weight",
                        row.intended, rules.sleeve_consequence, unit="% of equity")
            out.append(ItemCheck(row.symbol, check(rule, Measure.exact(weights.get(row.symbol,
                                                                                   0.0)))))
        sleeve_of = {row.symbol: row.sleeve for row in rules.sleeves}
        for total in rules.sleeve_totals:
            rule = Rule(f"3.total.{total.sleeve}", f"{total.sleeve}: intended total",
                        "sleeve weight", total.intended, rules.sleeve_consequence,
                        unit="% of equity")
            weight = sum(w for symbol, w in weights.items()
                         if sleeve_of.get(symbol) == total.sleeve)
            out.append(ItemCheck(total.sleeve, check(rule, Measure.exact(weight))))
    return out


def _netting(view: PortfolioView, rules: RuleSet) -> tuple[CoreNetting | None, str]:
    """A1: the book's β split into the core's and the rest's, and the core
    weight that would bring it into the Beta band. None, with why, when it
    can't be split; None and no reason when section 3 names no core."""
    if not rules.core:
        return None, ""
    if any(row.beta is None for row in view.rows):
        return None, "No benchmark history, so no β to split between the core and the rest."
    beta_rule = rules.rule("4.beta")
    band = (beta_rule.limit.lower, beta_rule.limit.upper) if beta_rule else None
    try:
        return core_netting({row.symbol: row.weight for row in view.rows},
                            {row.symbol: row.beta for row in view.rows}, rules.core, band), ""
    except ValueError as exc:
        return None, f"The core can't net the rest: {exc}."


def _drafts(previous: PositionSnapshot, current: PositionSnapshot) -> list[TradeDraft]:
    def quantities(snapshot: PositionSnapshot) -> dict[str, tuple[float, float | None]]:
        held: dict[str, tuple[float, float | None]] = {}
        for p in snapshot.positions:
            if p.kind is Holding.CASH:
                continue
            quantity, price = held.get(p.symbol, (0.0, None))
            held[p.symbol] = (quantity + p.quantity, p.price if p.price is not None else price)
        return held

    before, after = quantities(previous), quantities(current)
    drafts = []
    for symbol in sorted(set(before) | set(after)):
        old, old_price = before.get(symbol, (0.0, None))
        new, new_price = after.get(symbol, (0.0, None))
        if abs(new - old) > 1e-9:
            drafts.append(TradeDraft(symbol, old, new, new_price if new_price is not None
                                     else old_price))
    return drafts


def _finite(x) -> float | None:
    return float(x) if x is not None and math.isfinite(x) else None


def _payload(rules: RuleSet, snapshot: PositionSnapshot, files: Sequence[Path],
             checks: Sequence[ItemCheck], streams: Sequence[StreamVerdict],
             influential: Sequence[InfluentialDay], removed: Sequence[date],
             calm: CalmGate | None,
             volatility: float | None, view: PortfolioView,
             netting: CoreNetting | None = None, crash: CrashStress | None = None) -> dict:
    """What the journal keeps of a run: enough to show it again and to
    count section 1's runs, nothing the store would need to understand."""
    return {
        "rules": {"version": rules.version, "reason": rules.reason},
        "snapshot": {"as_of": snapshot.as_of.isoformat(), "name": snapshot.name,
                     "files": [p.name for p in files]},
        "equity": _finite(view.equity),
        "checks": [{"key": c.check.rule.key, "label": c.check.rule.label, "item": c.item,
                    "outcome": c.check.outcome.value,
                    "estimate": _finite(c.check.measure.estimate) if c.check.measure else None,
                    "error": _finite(c.check.measure.error) if c.check.measure else None,
                    "bound": _finite(c.check.bound), "z": _finite(c.check.z),
                    "consequence": c.check.consequence.value if c.check.consequence else None,
                    "reason": c.check.reason} for c in checks],
        "streams": {s.sleeve: bool(s.breaches) for s in streams},
        "verdicts": {s.sleeve: {"run": s.run, "grade": s.grade.value if s.grade else None,
                                "breaches": list(s.breaches)} for s in streams},
        "influence": [{"day": d.date.date().isoformat(), "mix": d.decides_mix,
                       "share": _finite(d.variance_share), "robust share": _finite(d.robust_share),
                       "holdings": {h.symbol: _finite(h.variance_share) for h in d.holdings}}
                      for d in influential],
        "removed_days": [d.isoformat() for d in removed],
        "gates": {"calm": _finite(calm.probability) if calm else None,
                  "volatility": _finite(volatility)},
        "netting": None if netting is None else {
            "core": list(netting.core), "core_weight": _finite(netting.core_weight),
            "core_beta": _finite(netting.core_beta), "rest_weight": _finite(netting.rest_weight),
            "rest_beta": _finite(netting.rest_beta)},
        "crash": None if crash is None else {
            "beta": _finite(crash.beta), "error": _finite(crash.error) if crash.error else None,
            "worst": _finite(crash.worst.depth), "peak": crash.worst.peak.isoformat(),
            "trough": crash.worst.trough.isoformat(),
            "band": [_finite(d) for _, d in crash.band] if crash.band else None,
            "data": [crash.first.isoformat(), crash.last.isoformat()]},
    }
