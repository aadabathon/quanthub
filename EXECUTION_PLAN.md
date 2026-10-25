# EXECUTION_PLAN.md — deploying algos, paper first

Proposal, 2026-10-24. Planning only: no app code changes. It asks you to
amend D12 and hard rule 1. Each milestone needs your approval before it
starts, as with [WORKSTATION_PLAN.md](WORKSTATION_PLAN.md) and
[LENS_PLAN.md](LENS_PLAN.md).

**Status, 2026-10-25.**
- Approved: the track, the amendment of D12 and hard rule 1, timing (b),
  and a dedicated account.
- E0 landed as D52. Its parts are the rewritten hard rule (AGENTS.md,
  CLAUDE.md), two layering checks that enforce it, and the settings and
  journal designs below.
- Still no order code.
- E1's code landed the same day (D54): read-only accounts, positions and
  balances, and Portfolio's From IBKR. What's left of E1 is yours: the
  `ibapi` 10.x install and the live check (`spikes/e1_check.py`).

## E0, settled (2026-10-25)

### When a run happens

Timing (b) in practice:
- The run happens on session t+1, before the closing-auction cutoff.
- Its decision reads stored bars through the close of session t only.
- It sends market-on-close orders for t+1.
- Running during the session, rather than the evening before, also lets
  the run reconcile against live positions and see the market open before
  it sends anything.

### The settings (implemented at E2)

A new `[execution]` section in `settings.toml`. Every money limit starts
unset, and an unset limit means nothing is sent.

```toml
[execution]
# off | paper | live. live is refused until E6's decision entry exists.
mode = "off"
# From Client Portal's paper settings. Paper IDs conventionally start "DU".
paper_account = ""
# Required for live only.
live_account = ""
# The runner's own IBKR client id (the GUI's is 17).
client_id = 18
# TWS defaults; IB Gateway uses 4002 (paper) and 4001 (live).
paper_port = 7497
live_port = 7496

# Money: unset (0) means nothing is sent.
capital = 0               # K: dollars the algo may hold
max_order_notional = 0    # dollars, per order
max_daily_notional = 0    # dollars, gross, per run

# Proposed defaults:
max_orders = 20           # per run
loss_stop = 0.05          # halt if net liquidation fell more than this since the last run
band = 0.005              # b: skip rebalances under this fraction of K
min_ticket = 250          # m: dollars; skip smaller orders
collar = 0.10             # c: price buffer in the cash check
submit_by = "15:40"       # US Eastern; NYSE's MOC cutoff is 15:50

# The saved Research run to deploy (E4 on).
experiment = 0
```

Why these defaults:
- **`max_orders` = 20.** Research universes are a handful of symbols, so
  20 only stops a runaway.
- **`loss_stop` = 5%.** A tripwire for "something is wrong today", not a
  risk budget. Your REGIME.md limits are the risk budget.
- **`band` = 0.5% of K.** It keeps price drift from generating trades
  every day.
- **`min_ticket` = $250.** IBKR charges a minimum per order: about $0.35
  on tiered pricing and $1 on fixed, for US stocks, at the time of
  writing (check your plan). That keeps a minimum ticket's commission
  under 0.4%.
- **`collar` = 10%.** Buys fill at the next close, so the cash check
  allows for a 10% move. SPY's largest day in the last two years was
  +10.5%, and a single stock can move further. A move past the collar can
  leave a small debit, which the journal records.
- **`submit_by` = 15:40 ET.** Ten minutes before NYSE's 3:50 pm cutoff.
  Nasdaq's is 3:55. E4 re-checks each listing's.

Validation, when the settings file is read:
- `mode` must be one of the three.
- `paper` needs `paper_account`; `live` needs `live_account` (and E6).
- Money values must be ≥ 0, and `max_orders` a whole number ≥ 1.
- `band` and `collar` must lie in [0, 0.5], and `loss_stop` in (0, 1).
- `submit_by` must fall between 09:30 and 15:45 ET.

Any problem is reported the way D36 reports settings problems, and leaves
`mode` off.

### The journal (implemented at E2, under D36's migrations)

These tables go in the same SQLite file as everything else:
- The journal is append-only, except for an intent's state and its
  broker IDs.
- Session dates are naive dates (D19). Events are UTC instants: orders
  are moments, not sessions.

```sql
CREATE TABLE execution_runs (
    id INTEGER PRIMARY KEY,
    session_date TEXT NOT NULL,       -- the session whose close the orders target
    mode TEXT NOT NULL CHECK (mode IN ('shadow', 'paper', 'live')),
    account TEXT NOT NULL,
    experiment_id INTEGER,            -- research_runs.id deployed (NULL for manual)
    fingerprint TEXT,                 -- that run's implementation and data fingerprint
    started_at TEXT NOT NULL,         -- UTC
    net_liquidation REAL,             -- account summary at the start
    cash REAL,
    status TEXT NOT NULL,             -- running | done | halted | failed
    note TEXT,
    UNIQUE (session_date, mode, account)  -- one run per session: a rerun resumes it
);

CREATE TABLE order_intents (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES execution_runs(id),  -- NULL: a manual ticket (E2)
    order_ref TEXT NOT NULL UNIQUE,   -- sent to IBKR as orderRef
    symbol TEXT NOT NULL,
    con_id INTEGER,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity REAL NOT NULL CHECK (quantity > 0),
    order_type TEXT NOT NULL,         -- MOC | LMT | MKT
    limit_price REAL,
    reference_price REAL NOT NULL,    -- P_i, the price the size was computed at
    target_weight REAL,               -- w*_i (NULL for manual)
    state TEXT NOT NULL,              -- see the state machine below
    blocked_by TEXT,                  -- the check that stopped it
    broker_order_id INTEGER,
    perm_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE order_events (           -- every callback and transition, as received
    id INTEGER PRIMARY KEY,
    intent_id INTEGER NOT NULL REFERENCES order_intents(id),
    at TEXT NOT NULL,                 -- UTC
    kind TEXT NOT NULL,               -- planned | blocked | sending | openOrder | orderStatus
                                      -- | error | adopted | not_sent
    status TEXT,
    filled REAL,
    remaining REAL,
    avg_price REAL,
    detail TEXT
);

CREATE TABLE fills (
    exec_id TEXT PRIMARY KEY,         -- IBKR's execution id: a duplicate changes nothing
    intent_id INTEGER REFERENCES order_intents(id),  -- NULL: not sent by us (flagged)
    at TEXT NOT NULL,                 -- UTC
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    shares REAL NOT NULL,
    price REAL NOT NULL,
    commission REAL                   -- from commissionReport, when it arrives
);

CREATE TABLE halts (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,                 -- UTC
    source TEXT NOT NULL,             -- kill switch | loss stop | check | reconciliation
    reason TEXT NOT NULL,
    rearmed_at TEXT                   -- NULL while halted; set only by hand
);
```

**`order_ref`.**
- A run's ref is `qh:<session date>:<first 8 characters of the
  fingerprint>:<symbol>`. It's deterministic, so re-deciding the same
  session gives the same ref, and the broker query finds the order.
- A manual ticket's ref is `qh:manual:<intent id>`.

**The state machine:**

    PLANNED → BLOCKED                         (a check failed: never sent)
    PLANNED → SENDING → ACKED → PARTIAL → FILLED
                              ↘ CANCELLED | REJECTED | EXPIRED
    SENDING → ADOPTED…                        (found at the broker after a restart)
    SENDING → NOT_SENT                        (not found, after the cutoff)

**Checked at E2:**
- how IBKR reports execution corrections: replacing a fill, or adding one
- that `orderRef` comes back intact in `openOrder`, completed orders and
  executions, on the 10.x API

## What you asked

"DEPLOYING ALGOS" matters to you. You asked how hard IBKR's trading API is
to learn, then asked for a paper-first plan.

**The short version:**
- The API is the easy part. It's the same callback model `broker/ibkr.py`
  already tames, plus about ten calls.
- The work is everything around it: knowing what was sent, surviving
  crashes without sending twice, limits the strategy can't override, and
  measuring how far live drifts from the backtest.
- The plan builds those in that order and runs on paper long before any
  live order.

## What exists to build on

- **The broker layer** (`broker/ibkr.py`):
  - turns IBKR's callbacks into blocking calls, correlated by request ID,
    with timeouts
  - classifies error codes
  - resolves contracts and option chains
  - live-verified against TWS
- **Research** (D41, D42):
  - six causal long/cash strategies (`models/strategies.py`) whose output
    is target weights
  - a self-financing daily backtest (`models/backtest.py`)
  - immutable saved runs with a fingerprint of the code, data and
    parameters
- **The regime** (W7, D49):
  - REGIME.md's limits and market-state gates, versioned in SQLite and
    checked daily on the Review page
  - its gates already speak of "block adds". Today that only changes the
    checklist, because nothing can trade.
- **Missing:** IBKR positions and account values. W1 was waiting for your
  IBKR account. Execution can't reconcile without them, so W1 becomes this
  track's first code milestone.

## Conflicts with the hard rules

1. **Hard rule 1 and D12:** "No order-placement code anywhere, under any
   flag", and execution is "a separate, owner-initiated design
   conversation with its own safeguards." This is that conversation.
   - Approving this plan amends both. The proposed wording is under "Doc
     changes on approval".
   - Until you approve, nothing changes.
2. **D12's "no auto-reconnect" stays.**
   - A run connects once, does its work and disconnects.
   - A dropped connection stops the run, and the next run reconciles.
   - Nothing retries a send.
3. **Dependencies.**
   - Placing orders should use the current API, which is version 10.x,
     installed from IBKR's own download page, not PyPI's 9.81. The
     read-only paths get re-verified on 10.x first (E1).
   - US Eastern time comes from the stdlib's `zoneinfo`, through `tzdata`,
     which pandas already requires. Nothing new is added.
   - No `ib_async`, and no login automator (IBC). An automator would have
     to store your IBKR password, and this plan doesn't.
4. **D19 (naive session dates) stands for bars.** The runner is the app's
   first code that needs the exchange clock, for cutoffs and run times.
   That clock is US Eastern, converted at the edge and never stored in
   bars.
5. **Scope:**
   - Stocks and ETFs, long or cash, the same universe Research can
     backtest. No options orders, no shorts, no leverage.
   - Options execution would be its own plan: combos, assignment, greeks
     limits.

## Design

### Where it runs

- **A headless runner process.** It's started once a day by Windows Task
  Scheduler, with its own IBKR client ID. One run:
  1. connect
  2. reconcile
  3. decide
  4. check
  5. send
  6. await fills
  7. journal
  8. disconnect
- **The GUI watches:**
  - an Orders page with the journal, the blotter and the kill switch
  - in E2, a manual order ticket for learning on paper
- **TWS (or IB Gateway) has to be up.**
  - IBKR's auto-restart keeps a session alive across days, but IBKR still
    asks for a fresh login every week, and sometimes mid-week.
  - With TWS down, the run fails closed and says so. It doesn't make a
    trade-less day look like a normal one.

### When it decides, and when it fills

Research decides at close *t* with data through *t*, and fills at close
*t*. `backtest.py` itself calls this "an idealized daily research
convention, not a claim that a signal observing the final closing print can
execute there." Live has two honest options:

- **(a) A near-close snapshot.**
  - Decide at about 3:40 pm ET from the last traded prices, and send
    market-on-close (MOC) orders.
  - NYSE takes MOC orders until 3:50 pm ET; Nasdaq stops at 3:55. The
    plan re-checks each listing's cutoff in E4.
  - It's closest to the research convention.
  - But the signal sees 3:40 prices, not the close, so live decisions
    can't be replayed exactly from stored bars.
- **(b) Yesterday's close, today's close (recommended).**
  - Decide after the close from complete daily bars, and send MOC orders
    the next session.
  - Every live decision is exactly reproducible from the store.
  - It costs one day of lag. Research gains an `execution_lag` option
    (targets shifted one session) so the backtest shows what the lag
    costs before any order exists.

### Which capital it trades

- **A dedicated account is cleanest.**
  - IBKR nets positions by symbol, so an algo trading SPY in the same
    account where you hold SPY by hand can't tell its shares from yours.
  - Recommended: a separate paper account, then a separate live account.
- **The fallback** is one account with the journal tracking the algo's own
  shares per symbol. Reconciliation then checks the broker holds at least
  that many. It works, but it's weaker.

### Every algo is a saved Research run

- The runner deploys a saved Research experiment, matched by its
  fingerprint: the same code, parameters and data source.
- If it can't be backtested here, it can't be deployed here.
- Refits follow the experiment's own schedule (`refit_every`), on data
  through the decision close, as the walk-forward runner already
  enforces.

### The pieces, and where they live

| Piece | Module | Rule |
|---|---|---|
| Order types: intent, state, fill, account | `data/orders.py` | broker-neutral port |
| IBKR orders: place, cancel, what-if, callbacks | `broker/orders.py` | the only module that calls `placeOrder`, `cancelOrder` or `reqGlobalCancel` |
| Sizing: targets to shares to orders, band, cash | `models/rebalance.py` | pure |
| Journal: intents, broker orders, fills, runs, halts | `storage/order_journal.py` | schema +1 (D36) |
| Checks, reconciliation, kill switch | `services/execution.py` | the only caller of `broker/orders.py` |
| The daily run | a runner module, named at E4 | headless |
| Ticket, blotter, journal, kill switch | `gui/pages/orders.py` | calls services only |
| Simulated exchange | `tests/fake_exchange.py` | deterministic |

`tests/test_layering.py` gains two static checks:
- the order calls appear nowhere but `broker/orders.py`
- only `services/execution.py` (and the composition root) import it

## Safety, in layers

Each layer stops a different mistake. Any failure stops the run before its
next send. Nothing fails open.

1. **Mode.**
   - `[execution] mode` in the settings file is `off` by default.
   - `paper` requires the connected account to equal `paper_account` from
     the settings. Paper IDs conventionally start with DU, which is
     checked too, as a second signal, not the rule.
   - `live` requires its own decision entry, plus `live_account` matching
     exactly. It ships disabled.
2. **The journal comes first.**
   - Every order is written as an intent before it's sent.
   - It's tagged with an `orderRef` derived from the run date, the
     experiment's fingerprint and the symbol.
   - Nothing is ever sent twice (the next section shows how).
3. **Pre-trade checks.** They're deterministic and outside the strategy:
   - symbols allowlisted from the experiment
   - no shorts, no options
   - cash covers the buys at a price collar
   - limits on each order's notional, the day's gross notional, and the
     orders per run
   - REGIME.md's limits evaluated on the *pro forma* book
   - a "block adds" gate means no buys today
   - a fail-grade breach halts the run
4. **Daily loss stop.** If net liquidation is down more than your limit
   from the previous run, nothing new is sent. You re-arm by hand.
5. **Kill switch.**
   - A halted flag in SQLite, checked at the start of each run and before
     each send.
   - The GUI button sets it, and with a connection also cancels every
     open order (`reqGlobalCancel`).
   - Re-arming is a deliberate click.
6. **TWS's own Precautionary Settings:** size and value limits inside TWS,
   set by you, independent of this code.
7. **Who places orders.** I build and test against the simulated exchange.
   You run paper, and you decide what goes live. I don't place orders on
   your accounts.

## The math

### Targets to orders

The quantities:
- K: the dollars assigned to the algo
- w*ᵢ: the strategy's target weights, long-only, with Σᵢ w*ᵢ ≤ 1
- Pᵢ: the reference price (yesterday's close under timing (b))
- qᵢ: the algo's reconciled shares

The target shares round toward zero, so a target is never overshot:

    q*ᵢ = ⌊w*ᵢ K / Pᵢ⌋,        Δᵢ = q*ᵢ − qᵢ.

**No-trade band.** Skip an order when |Δᵢ| Pᵢ < max(m, bK), with m the
minimum ticket and b the band fraction. That stops a day's drift from
generating tiny trades.

**Weight error.** Rounding alone leaves 0 ≤ w*ᵢ − wᵢ < Pᵢ/K. A skipped
order leaves the held shares less than max(m, bK) of value from the
rounded target. So in all, |wᵢ − w*ᵢ| < Pᵢ/K + max(m/K, b). At K = $10,000, a $500 stock can sit 5
points under its target from rounding alone. The shadow run (E5) reports
it, and the backtest can be re-run with the same rounding.

**Cash.** Buys fill at today's close, not at Pᵢ. With a collar c covering
the move from Pᵢ, the run sends only if

    Σ_buys Δᵢ Pᵢ (1 + c) + fees  ≤  cash + Σ_sells |Δᵢ| Pᵢ (1 − c).

A close beyond the collar can still overdraw slightly. The setting makes
that a choice, and the journal records any shortfall.

### Never sending twice

Each intent moves through:

    PLANNED → BLOCKED                       (failed a check: never sent)
    PLANNED → SENDING → ACKED → PARTIAL → FILLED
                              ↘ CANCELLED | REJECTED | EXPIRED

**SENDING is written before `placeOrder` returns.**

**On restart, an intent left in SENDING is matched by its `orderRef`**
against the broker's open orders, completed orders and the day's
executions:
- Found: the order is adopted, at whatever state the broker reports.
- Not found, before the cutoff: it may be sent once more, and only if
  every query answered.
- Not found, after the cutoff: it's marked as never sent.

Fills are keyed by IBKR's execution ID, so duplicate callbacks change
nothing.

**The test:** crash the runner at every write, restart it, and assert the
broker received each intent at most once.

### Measuring live against the backtest

**Implementation shortfall** per order, in basis points, with side sᵢ (+1
for a buy, −1 for a sell), fill Fᵢ and benchmark Bᵢ:

    ISᵢ = sᵢ (Fᵢ − Bᵢ) / Bᵢ × 10⁴.

There are two benchmarks:
- the official close: the auction's quality, about 0 for MOC
- the decision price: what the lag costs

**Tracking difference.** It's dₜ = r^live_ₜ − r^bt_ₜ, against the backtest
re-run over the live sessions from the live starting book. Its mean d̄ is
the drag, with standard error s_d/√N. Resolving a drag δ at two standard
errors takes

    N ≥ (2 s_d / δ)²  sessions.

At s_d = 10 bps a day:
- δ = 5 bps takes 16 sessions
- 2 bps takes 100
- 1 bp takes 400

2 bps a day is about 5% a year (×252). So small daily drags matter, and
they take months to see.

**Paper can't measure this.** IBKR says paper fills are simulated and may
not match the live market. Paper proves the plumbing. Only small live size
measures costs.

## Milestones

Each needs your approval, as before. E0 is docs only.

**E0. The decision.**
- Amend D12 and hard rule 1, using the wording below.
- Settle the settings: mode, accounts, limits, and the band and collar.
- Design the journal's schema for review.
- No order code.

**E1. Accounts, read-only (W1 absorbed).**
- `ibapi` 10.x from IBKR's download, with the existing read-only paths
  re-verified on it.
- Add `reqManagedAccts`, `reqPositions` and `reqAccountSummary` (net
  liquidation, cash) to the broker adapter.
- `ibkr` joins Portfolio's account selector, as W1 planned.
- Tested against the fake, then live-verified read-only on your paper
  and live logins.

**E2. Orders on paper, by hand.**
- `data/orders.py`, `broker/orders.py`, `storage/order_journal.py`,
  `services/execution.py`, and an Orders page:
  - a ticket for one stock or ETF order (market, limit or MOC)
  - a what-if preview first: margin and commission, with nothing placed
  - the blotter and the journal
- Mode `paper` only.
- Tests:
  - the simulated exchange: acknowledgements, partial fills, rejects,
    duplicate statuses, a disconnect mid-order
  - recorded callback transcripts from your paper account, replayed
- This is where you learn the API by using it.

**E3. Reconciliation and the kill switch.**
- At startup, journal against the broker: positions, open orders, the
  day's executions. Anything unknown is flagged, never adopted silently.
- The crash-at-every-write test.
- The kill switch and the loss stop.

**E4. The runner, in shadow mode.**
- The headless daily run against a saved experiment:
  - it decides, sizes, checks and journals
  - it sends nothing
- The research `execution_lag` option lands here.
- Its decisions must equal the backtest's on the same data, exactly. That
  is the check that the deployed algo is the researched one.

**E5. The runner on paper.**
- It sends MOC orders on paper, honoring REGIME.md's gates and limits.
- It runs for the number of sessions you choose. At least 20 is suggested,
  to exercise the plumbing: month-end, a holiday, a TWS restart, a
  re-login.
- It reports implementation shortfall and the tracking difference.

**E6. Live.**
- Its own decision entry, a dedicated live account, a small capital cap,
  and the same runner and limits.
- You turn it on. The first weeks measure real costs against the N
  formula above.

Order and overlap:
- E0 and E1 touch different files from LENS_PLAN's V1, L and W2b, so they
  can run alongside them.
- E2 onwards needs E1.

## Decisions for you

1. ~~**Approve the track,** and with it the amendment of D12 and hard
   rule 1.~~ Approved 2026-10-25 (D52).
2. ~~**Timing (b),** yesterday's close to today's close.~~ Approved.
3. ~~**A dedicated account,** paper then live.~~ Approved.
4. **Upgrade `ibapi`** to 10.x from IBKR's download. It's needed for
   orders, and E1 re-verifies the read-only paths. Confirmed at E1's
   start.
5. **The first limits.** E0 proposes the band, collar, minimum ticket,
   order count and loss stop (above). The money limits are yours: capital,
   per-order notional, and daily gross notional.
6. **Paper length** in sessions: at least 20 for the plumbing. Live costs
   are measured live.
7. **Where this sits against LENS_PLAN:** E0 and E1 alongside V1, or
   after it.
8. **Your IBKR logins.** E1 needs a paper login linked to your account. Is
   it set up?

## Doc changes on approval

E0 made these on 2026-10-25 (D52). Later milestones add their own entries.

- **AGENTS.md, hard rule 1** becomes:

  > "Orders exist only on the execution path: `broker/orders.py`, called
  > only by `services/execution.py`. They're off unless the settings file
  > enables them, paper unless a live decision and account match. Every
  > order is journaled before it's sent, nothing is sent twice, nothing
  > reconnects or retries a send, and every deployed strategy is a saved
  > Research run."

- **DECISIONS.md:**
  - a new entry that amends D12 for this track
  - entries for timing, accounts, the journal and the limits, each
    written as its milestone lands
- **ROADMAP.md:** "Paper trading, then execution" moves from "After v1.0"
  into this track, E0–E6.
- **ARCHITECTURE.md:** the execution path, the runner and the journal.
- **`tests/test_layering.py`:** the two static checks above.
- **CURRENT_STATE.md:** each milestone as it lands.

Sources checked for the IBKR facts above (2026-10-24):
- [IBKR TWS API: placing orders](https://www.interactivebrokers.com/docs/tws-api/doc/quick-start/placing-orders.md)
- [IBKR: daily and weekly reauthentication](https://www.interactivebrokers.com/docs/tws-api/doc/tws-settings/daily-weekly-reauthentication)
- [IBKR: paper fills that don't match the market](https://ibkb.interactivebrokers.com/article/252)
- [NYSE closing auction fact sheet](https://www.nyse.com/publicdocs/nyse/NYSE_Auctions_Closing_Process_Fact_Sheet.pdf)
- [Nasdaq closing cross FAQ](https://www.nasdaqtrader.com/content/productsservices/Trading/ClosingCrossfaq.pdf)
- [IBKR: finding your paper credentials](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-users/finding-your-paper-user-credentials/step-five-review-your-paper-credentials)
