# Data System — Improvement Candidates

Working notes from a review of `data/`, `storage/`, `services/` (2026-08-16).
Not a plan — a menu. Per [AGENTS.md](AGENTS.md), nothing here gets built
without picking one item, discussing scope, and doing it as its own
milestone. Cross-references: [ROADMAP.md](ROADMAP.md),
[DECISIONS.md](DECISIONS.md), [CURRENT_STATE.md](CURRENT_STATE.md).

## Already tracked (don't re-propose these — just pointers)o

- End-"now" fetch semantics (D11) — lifting this is a named ROADMAP
  near-term candidate ("pinned historical end-times").
- Intraday bars rejected until a timezone policy exists (D19).
- Quote refresh is serial, one TaskRunner worker (D9) — explicitly flagged
  as a limit in CURRENT_STATE.md.
- No retry/backoff on broker fetches — M4 known limit.
- Frequency-aware annualization (currently 252/yr always) — ROADMAP "Later".

## New observations from this review

### 1. Corporate actions are an open question, not just an untested edge

`data/bars.py` and `storage/bar_store.py` treat every bar as a plain
observation. Nothing in the schema or docs says whether stored closes are
split/dividend-adjusted. IBKR's `reqHistoricalData` returns **unadjusted**
splits by default (adjustment depends on the request flag, which
`broker/ibkr.py` would need to set explicitly). A silent stock split shows
up as a ~2x overnight "return" that will poison `data/stats.py` moments,
GARCH fitting, and any drawdown calc the moment real multi-year histories
accumulate splitting names (AAPL, TSLA, NVDA all have splits in typical
backtest windows).

This isn't a bug to fix reactively — it's a modeling decision (raw vs.
adjusted vs. both-stored) that should be made deliberately, the way D19
made the timezone call deliberately, before real backtesting work lands on
top of it.

**Candidate:** a short design note (owner + Claude) deciding adjustment
policy, landing as a DECISIONS entry before the backtester roadmap item.

### 2. No linkage across symbol identity changes

`InstrumentKey` is `(symbol, sec_type, exchange, currency)` — correct for
"don't conflate a STK and a FUT with the same ticker" (the documented
rationale), but it has no notion of a ticker rename (FB→META,
delisting/relisting). Today that's invisible because the store only holds
SPY-like unchanging names; worth a one-line note in `bar_store.py`'s
docstring now, and a real answer only when the first renamed symbol is
actually requested — no need to build for it yet.

### 3. Validation reports are logged, not retained

`data/bars.py`'s `ValidationReport` (dropped/repaired rows, reasons) is
rich but currently only reaches a log line via `CachedSource._sync_missing`
(`services/cached_source.py:113-117`). Once something is dropped, there's
no queryable record of *which* historical bars were rejected and why — if
a symbol's data looks wrong six weeks later, the diagnosis is "grep old
logs" instead of a query. A `validation_issues` table (or even a flat file)
keyed by (instrument, source, ts) would make the existing validation work
actually inspectable after the fact, at low cost since the data already
exists in-memory at the point it's discarded.

### 4. Single-symbol serial sync doesn't scale to a real watchlist

`CachedSource._sync_missing` and the D9 single TaskRunner worker mean
backfilling N symbols' histories is N sequential IBKR round-trips. Fine at
today's scale (a handful of watchlist names); becomes the bottleneck the
moment "sync my whole watchlist" or a systematic universe (e.g., S&P 500
backfill for model research) shows up. D9 already names its own revisit
condition: *"only when something genuinely needs parallel fetches."*
Universe-scale backfill is plausibly that trigger — worth flagging now so
it's a deliberate choice when the first model module needs broad data,
not a scramble later.

### 5. No file/CSV `BarSource` yet, though the seam already exists

D20's port/adapter split (`BarSource.fetch_bars` independent of
`DataSource.get_history`) was explicitly built so "IBKR/CSV future flows
through the identical pipeline." Nothing implements it for files yet. A
CSV/Parquet importer would (a) let the owner backtest against
already-owned vendor data without waiting on IBKR entitlements, and (b)
be a good exercise in stress-testing `validate_ohlcv_frame` against messier
real-world data than IBKR's own feed produces. Small, self-contained,
fits the existing seam — good size for a single milestone.

### 6. No automated backup/versioning story for the SQLite file

**Done in F1 (2026-10-15, D36):** schema versions and a backup before any
migration.

`~/.quant_hub/market_data.db` is the sole copy of every acquired
observation once persistence-only history (no re-derivable synthetic
data) starts accumulating months of real backfills. There's currently no
copy-before-schema-migration step and no periodic backup. Doesn't need
infrastructure (D17 already bans that) — even a "copy the file before
`BarStore.__init__` runs a schema change" convention, or a manual
`.backup()` call via `sqlite3`'s own online backup API, would cover the
realistic risk (a bad migration corrupting months of backfilled history)
for near-zero cost.

### 7. `coverage` capping assumes daily bars specifically

**Done in F1 (2026-10-15, D36):** `CachedSource` rejects non-daily bar
sizes.

`CachedSource._record_coverage` caps coverage at *yesterday* using
`floor_day`, which is correct for daily bars but is hard-coded to
day-granularity — nothing currently stops `bar_size="1 week"` from being
passed through (`services/cached_source.py` takes `bar_size` as a plain
string) even though ROADMAP's "expose weekly/monthly" item hasn't landed
and D19 only defined the daily case. Worth a guard (reject non-daily
`bar_size` at the `CachedSource` boundary, mirroring the intraday
rejection already in `broker/ibkr.py`) so the gap is a loud rejection, not
a silent wrong-coverage-window bug, whenever someone reaches for "1 week"
before the real feature exists.

## Suggested ordering (for discussion, not decided)

1. **Corporate-actions decision** (#1) — cheapest to resolve now, most
   expensive to resolve retroactively (every stored bar's meaning depends
   on the answer).
2. **Non-daily `bar_size` guard** (#7) — a few lines, closes a real gap
   between what the type system allows and what D19 actually validated.
3. **CSV `BarSource`** (#5) — self-contained milestone, immediately useful
   for backtesting against owned data, exercises the existing seam.
4. **Validation-issue retention** (#3) — moderate size, pays for itself
   the first time a stored series looks suspicious.
5. **Backup convention** (#6) and **parallel sync** (#4) — lower urgency
   at current data volume; revisit when backfill scope or history depth
   actually grows.
