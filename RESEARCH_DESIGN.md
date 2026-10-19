# Research workspace — design and v1 implementation

Prepared 2026-10-18 after the owner's request for an open-ended place to
conduct research, run backtests, and keep their papers. This is a product
proposal, implemented as a lean v1 workspace on 2026-10-19 after the owner's
“ship v1” instruction. R1's engine/statistics landed on 2026-10-18 (D41);
R2, R3 and the local catalog followed (D42). The broader ideas below remain
design direction where the v1 scope does not claim them.

V1 includes six strategies, three controlled worlds, risk sizing, rolling
training/refits, matched buy-and-hold, exact saved prices and decisions,
notes, parameter grids and IID-labelled DSR. The paper catalog includes
local PDF copies, metadata, searchable notes and experiment links. Source
SHA-256 captures uncommitted implementation changes as well as releases;
saved decisions replay without rerunning model estimation. Failed jobs
report their error; completed grid trials survive a later grid failure.

Later work includes side-by-side run comparison, automated cost sensitivity,
full-text PDF search/annotations, acquisition of a broader literature
catalog, and personalized links to present portfolio decisions. A real
Fidelity export and a fresh adjusted-cache TWS check remain external
acceptance checks.

## Purpose

Keep the whole research chain inspectable:

    paper → hypothesis → model or rule → experiment → evidence → conclusion

An experiment may succeed, fail, or stay inconclusive. All three outcomes
are useful and should be kept. The page should support new questions as
the owner's research grows, without requiring a new GUI for each strategy.

## One Research page, three work areas

### Experiments

- Dataset/source, instruments, price basis, date window, and benchmark.
- A named rule with a small, explicit parameter form. Start with the
  existing roadmap: momentum and mean-reversion baselines, Kalman,
  GARCH volatility targeting, and Markov regime filtering.
- Explicit training, refitting, and test settings; costs and sizing beside
  the strategy, not hidden in defaults.
- Performance: equity against the benchmark, drawdown, exposure, costs,
  turnover, and the roadmap's return/risk statistics.
- Robustness: results across test windows, parameter sensitivity, cost
  sensitivity, and the trial history used by the deflated Sharpe calculation.
- A position/trade ledger that explains when and why exposure changed.
- The owner's hypothesis, linked references, observations, and conclusion.

The initial implementation uses the existing daily-data scope. New rules
remain Python functions in the model layer; adding a rule does not require
an embedded code editor, new language, or plugin framework.

### Saved runs

- Every completed run gets an identity and is retained in SQLite.
- Reopen its configuration, results, diagnostics, references, and notes.
- Record failed and inconclusive experiments alongside promising ones.
- Compare runs over matching samples and assumptions, with the differences
  visible. Design the saved record first; comparison can grow incrementally.

Reproducibility needs more than a symbol and date range. Acquired histories
can be replaced by re-imports and adjusted prices can be rewritten (D38).
The persistence design must retain the exact model input data or reference
an immutable data snapshot, with a fingerprint, source/basis, parameter and
model versions, seed, and software revision/dirty state. Storing only the
current Git commit would miss the owner's uncommitted changes.

### Papers

A local research library: the owner's PDFs and a curated reference catalog,
organized by title, author, year, topic, reading status, and personal notes.
Each record can link to a model, an experiment, and a replication result.

The first increment should cover manual metadata, local PDF association,
source URL/DOI, search/filter, opening the paper, notes, and reading status.
Keep paper files outside the repository, beside other personal app data;
use SQLite for the catalog. A missing PDF should be repairable by relinking
the file without losing notes or references. A source-only record is valid.

Foundation topics include stochastic processes, asset pricing and factors,
portfolio theory, volatility/derivatives, time series, market microstructure,
and research methodology. The catalog can span early foundational work and
current papers; it should grow through useful references and replications.

Full-text search, PDF annotations, citation graphs, automatic metadata
retrieval, and bulk literature acquisition are possible later increments.
They are not prerequisites for the backtester or the v1.0 release.

## Requirements that earn trust

- Timing: a decision based on information through close t can earn only
  later returns. Define and test the execution/cost convention explicitly.
- Fitting is part of the strategy. Filtered Kalman/Markov outputs still
  leak future information if their parameters were fitted to the full test
  history. Train/refit on the information available at each evaluation date.
- Benchmarks use the same dates, price basis, financing assumptions, and
  reporting conventions as the strategy.
- Show gross return, net return, and costs separately. Derive turnover,
  rebalance accounting, cash treatment, and any leverage before coding.
- Preserve uncertainty and failed checks. Statistical diagnostics describe
  evidence; they do not turn a backtest into a trading recommendation.
- Validate on both planted-edge and null synthetic worlds before attaching
  real history and the GUI, as specified by the existing roadmap.

## Incremental build

1. **R1:** review the mathematical accounting and API, implement the pure
   daily backtester and statistical diagnostics, and validate hand-computed
   cases, timing, costs, and test splits.
2. **R2:** implement the named strategy functions with causal training and
   refitting, plus the synthetic validation worlds. Review each model's
   decision rule and sizing assumptions before its implementation.
3. **R3:** build Experiments and Saved runs with the existing Qt components
   and TaskRunner; persist enough information to reproduce each result.
4. **Paper library:** the lean increment was included in the owner's v1
   authorization; richer document features remain later work.
5. **v1.0 stabilization:** verify the full workflows, finish the real
   Fidelity import and adjusted-cache live checks, reconcile docs/version,
   and check the clone-and-use path without a broker.

## Personal use after the foundation

Extend the saved workspace with the owner's universes, position limits,
holding periods, costs, benchmarks, preferred research rules, and review
cadence. Link an approved experiment to current holdings so its present
inputs, state, and portfolio effect can be inspected at review time.

That later workflow should preserve the research record: which rule and
parameters were selected, when they were selected, which subsequent data
were new, and whether the observed outcome supports the original thesis.
It remains decision support within the project's read-only posture.
