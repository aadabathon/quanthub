# SLEEVE_PLAN.md — an active sleeve: your themes, traded by a machine

Proposal, 2026-10-29. Planning only: no app code changes. It asks you to:
- amend D52, the execution track's scope
- replace [EXECUTION_PLAN.md](EXECUTION_PLAN.md)'s E4–E6 with S1–S7

E2 and E3 stay, with one change to E2. Every safety layer in D52 stays.
Each milestone needs your approval before it starts, as with the other
plans.

**In one screen:**
- **The sleeve is a separate, active book,** 5–15% of your capital, in its
  own IBKR account. It's meant to be orthogonal to your core, act as a
  hedge, and keep this a passion.
- **You write themes:** a direction in a sentence or two, a budget, the
  instruments allowed, and an end condition or none.
- **The machine trades inside them, on its own:**
  - it picks instruments, sizes, hedges, enters, exits and rolls
  - it writes the memos, and you read a weekly digest
  - you can veto a position or end a theme at any time
- **Hard limits carry the safety,** and the machine can't change them. It
  can't create a theme, so it can't invent exposure you didn't ask for.
- **Grading needs no paperwork from you.** Each theme has a passive twin:
  the same direction, held the simplest way.
  - The twin's return says whether your idea was right.
  - The active book's return against the twin says whether the machine
    added anything.

## What you asked

In the execution-track conversation of 2026-10-29:
- An algorithmic trading dashboard, through IBKR's trading API. Here that's
  the Sleeve page.
- "I don't want to blindly hold US equity beta. I want to hold the
  tailwind of the future."
- Options, futures when necessary, short selling, and most trades taken
  by an algorithm or an LLM.
- 5–15% of the book, traded orthogonally and actively, "both as a passive
  hedge and to keep this a passion".
- "Nothing is off limits, it just has to be justified, and have a time
  frame and thesis associated."
- On a draft with one written thesis per trade, four corrections:
  - too much paperwork
  - the algo should act alone
  - theses are bigger than trades
  - not every bet ends

## What the evidence says (scratch run, 2026-10-29)

**The test.**
- The data: Ken French's daily US market file, 1926-07 → 2026-08. The
  market is all US stocks, weighted by value, not SPY.
- The rules: each one is taken from its paper, untuned. A sleeve holds the
  market on its signal days and T-bills otherwise, at timing (b)'s lag.
- Alpha is against the market, with Newey–West t-statistics in brackets.
  α > 0 is exactly the condition for a small sleeve to raise a market
  book's Sharpe ratio: with β = ρσ_a/σ_m, α = μ_a − βμ_m > 0 ⇔ S_a > ρS_m.

| Rule | In market | Orders/yr | α 1927–87 | α 1988–2007 | α 2008–26 |
|---|---|---|---|---|---|
| Turn of month (last day, first 3) | 18% | 24 | 5.7% (7.3) | 4.3% (3.0) | 0.6% (0.3) |
| Day before a holiday | 4% | 18 | 2.8% (8.5) | 0.6% (1.2) | 1.2% (1.8) |
| Above the 200-day average | 73% | 5 | 4.5% (3.9) | 1.3% (0.8) | 4.6% (2.1) |

Reproduce it (gitignored, local):

    cd spikes/target
    py -3.12 -I -X utf8 sleeves.py data/ff_daily.csv

**What it says:**
- **Published calendar effects decay.**
  - The turn of the month had a t of 7 for sixty years and survived its
    first papers (Ariel 1987; Lakonishok and Smidt 1988).
  - It's gone since McConnell and Xu (2008) made it famous.
  - McLean and Pontiff (2016) find that's typical: about half of a
    published effect's return is gone after publication.
- **Trend survived publication.** Its alpha since 2008 comes mostly from
  sitting out 2008 and 2022, so it rests on a few episodes. Research's own
  Momentum rule shows the same family (α 2.5–4.7% since 2008 at lookbacks
  of 63–252 days).
- **Commissions decide small sleeves.** At K = $1,000 and $0.35–$1.00 per
  order, a strategy trading twice a month pays 0.8–2.4% of K a year.
- **Caveats:**
  - Nine variants were run, so the post-2008 t's near 2 don't survive a
    multiple-testing haircut. The century-long t's do.
  - Commissions here leave out the exchange fees on closing-auction
    orders.

**Your "tailwind" has a name and a long record:** time-series momentum
across asset classes.
- **What it is:** hold what's rising, and short or step aside from what's
  falling, across stocks, bonds, commodities, currencies and crypto
  (Moskowitz, Ooi and Pedersen 2012).
- **The record:**
  - Hurst, Ooi and Pedersen (2017) find it positive in every decade back
    to 1880.
  - It did well in most of the largest drawdowns of a 60/40 portfolio.
  - Its correlation with equities is near zero on average, and it's short
    by the time a slow crash is deep.
- **Its limits:** it gets in late and out late, and it doesn't hedge fast
  crashes (1987, March 2020).
- **What it needs:** many unrelated markets and the short side. A
  concentrated, long-only "top tailwind" sleeve has neither. It would hold
  US equities most of the time, and add beta to a core that already holds
  them.

The run above is US-only. The cross-asset claim rests on those papers. S2
tests it on the ETFs the store can hold.

## The design

### Two levels: themes and positions

**A theme is yours.** It's long-lived, and you write it in a couple of
minutes:
- **Direction:** a sentence or two. "Follow the tailwinds outside my
  core." "Debasement." "This company, minus the market."
- **Budget ℓ:** the share of the sleeve's capital K the theme may lose
  under stress (see the math).
- **Instruments:** any of shares and ETFs, shorts, options, and futures
  (by the size rule).
- **Universe:** optional. Symbols, or "anything".
- **End condition:** one of
  - a condition the machine can check ("the 200-day trend breaks", "real
    yields above x for n weeks")
  - "until I end it"
  - a date
- **Engine:** rules (a saved Research run) or the LLM.
- **Twin:** the passive expression, which the machine proposes and you
  confirm (below).

Changing a theme makes a new version. The old one is kept, as REGIME.md's
versions are (D49).

**A position is the machine's.**
- It belongs to one theme.
- It carries its instrument, side, size, stress loss, hedge and a short
  memo.
- Options and futures add an expiry, which the machine closes or rolls
  before it arrives.

A theme with no end can hold positions that do end.

**Examples, generic:**

| Theme | Engine | Instruments | Ends | Twin |
|---|---|---|---|---|
| Tailwinds: cross-asset trend outside the core (gold, commodities, Treasuries, international and emerging equities, the dollar, bitcoin) | rules | ETFs, long or short | never | a managed-futures ETF at the same budget |
| Debasement | LLM | gold and bitcoin ETFs, calls | real yields rise for n weeks, or you end it | half gold ETF, half bitcoin ETF |
| One company, market-hedged | LLM | the stock, an index short or put | a stated event or date | the stock minus β × the index, rebalanced monthly |

Tailwinds' twin answers a question directly: does your own trend
following beat buying the professional version?

### Engines: who manages the positions in a theme

- **Rules.**
  - A saved Research run, as D52 requires today. Its decisions are
    reproducible from the store, and its live decisions must equal its
    backtest's on the same data (E4's check, kept).
  - Research is long/cash today. S2 adds shorts and a cross-asset
    universe.
- **The LLM.**
  - **Input:** the theme, the app's own data (prices, positions, regime
    state, option chains), and the limits. Nothing from the open web: a
    page can carry text written to steer it.
  - **Output:** a structured proposal. That's target positions, each with
    its instrument, side, size, stress loss and a memo saying what would
    prove it wrong.
  - **The checks:** deterministic code checks the proposal, as it checks
    any order. A proposal that fails is rejected whole and journaled.
  - **What it may not do:** create or change a theme, a budget or a limit,
    call the broker, or skip the journal.
  - **It can't be backtested.** The model has read the history it would
    be tested on. Its record is forward only: shadow first, then paper.
  - **Its decisions aren't repeatable.** Every call is journaled in full:
    the exact inputs, the model, and the response.

### Acting alone, inside hard limits

**Approval** is a setting, `each` or `digest`:
- **`each`:** the machine proposes and you approve every order. This is
  S3–S5's mode.
- **`digest`:** the machine sends within the limits, and you read the
  weekly digest. It's refused before S6. Live `digest` needs its own
  decision entry.

**Hard limits,** in settings the machine can't change:
- **Capital K:** the dollars the sleeve may use. The runner takes
  K = min(your setting, the account's net liquidation). Losses shrink K
  on their own; gains don't grow it until you raise the setting.
- **Budgets:** each position's stress loss counts against its theme's
  budget, and the budgets sum to at most 1 (the math).
- **Gross cap G:** gross exposure ≤ G·K.
- **Per order and per day:** notional caps and an order count, as
  EXECUTION_PLAN set them.
- **Losses:** EXECUTION_PLAN's daily loss stop, plus a drawdown stop on the
  sleeve from its high. Either one halts the machine until you re-arm it by
  hand.
- **Losses are capped:** no naked short options. Shorts of stock carry a
  resting buy-stop at the broker, journaled like any order.
- **The kill switch:** E3's, unchanged.

### The cadence

- **A weekly decision.** It runs after Friday's close, from stored bars
  through Friday:
  - each engine proposes
  - the checks run
  - orders go out Monday: closing-auction (MOC) orders for shares, and day
    limit orders for options during the session
  - an unfilled option order is cancelled before the close and journaled.
    Monday's leftover is a new intent on Tuesday, never a re-send.
- **A daily risk run,** every session. It's deterministic, with no LLM:
  - reconcile with the broker
  - check end conditions, budgets and stops
  - close or roll positions ahead of their expiry
  - its orders only ever reduce risk
- **Trend moves over weeks to months,** so weekly loses little. It also
  cuts commissions, the main cost at small K, and the chances to fiddle.
- **The pattern-day-trader rule doesn't bind** a weekly cadence. Reports
  say it changed in 2026, unchecked here.

### The weekly digest

It's stored in SQLite and shown on the Sleeve page. Nothing is emailed.
- what changed, and the machine's memo for each change
- each theme's P&L against its twin, and its budget in use
- anything within 20% of a limit
- expiries and rolls coming up, and end conditions close to firing

### Size and instruments

| Instrument | Workable from | Why |
|---|---|---|
| Shares and ETFs, long | any K | Whole shares: one share at price P is P/K of the sleeve, so a small K holds few names |
| Shorts | account equity ≥ $2,000 | IBKR gives margin only above USD 2,000 of equity (regulation). There are borrow fees, and the stress rule below |
| Options, with capped loss | a K in the low thousands | One contract's premium. About $0.65–0.70 a contract, with a $1 minimum per order (check your plan) |
| Futures (micros) | K ≥ s × notional / ℓ | One micro S&P contract is $5 × the index (CME specs; re-checked at that milestone). Micro gold is 10 oz, micro bitcoin 0.1 coin |

**At K below $2,000,** the dedicated account needs extra cash for margin
that the runner never uses.

**Futures, for example:** a micro index contract at an index level of
6,000 is $30,000 of notional. With s = 0.5 and ℓ = 0.25, it needs K ≥
$60,000. Below that, the engine has to use the ETF instead.

**Paper has no size limit,** so the whole design can run there first.

## The math

### Stress loss and budgets

Each position i gets a stress loss Lᵢ, computed when it's opened:

| Position | Lᵢ |
|---|---|
| Long shares, ETF or future | s_long × notional (proposed s_long = 0.5: a halving) |
| Short shares, ETF or future | s_short × notional (proposed s_short = 1.0: a doubling) |
| Long option, debit spread | the premium paid (the true maximum) |
| Credit spread | the width minus the credit (the true maximum) |
| Short option without cover | refused |

Theme j, with budget ℓⱼ, and the sleeve:

    Σ_{i ∈ j} Lᵢ ≤ ℓⱼ K,      Σⱼ ℓⱼ ≤ 1,      Σᵢ |notionalᵢ| ≤ G K.

The middle inequality means that if every theme hit its stress at once,
the sleeve would lose at most K. K is the money you've set aside to lose.
Options count toward the gross at their delta notional:
|Δ| × multiplier × underlying price.

**For example:** at K = $1,000 and ℓ = 0.25, a theme may hold $500 of a
long ETF (L = 0.5 × 500 = $250), or $250 of a short (L = 1.0 × 250), or
calls costing up to $250.

### The market hedge for one company

The hedge ratio is h = β̂ from a trailing regression (`models/regression.py`).
The theme holds the stock and short h × its value of an index ETF, or a
put. What's left is the company's own return.

At small K the hedge is coarse: one share of the index ETF can be a large
part of the position. The memo states the hedge error.

### Grading

**Per theme**, by week t:

    d_t = r_active,t − r_twin,t.

- The mean d̄ is the machine's value added, with standard error s_d/√N.
- Resolving a value added of δ at two standard errors takes
  N ≥ (2 s_d / δ)² weeks.
- At s_d = 2% a week and δ = 0.5% a week, that's 64 weeks.
- The twin's own return grades your idea. Its sign against the core in
  the core's bad months is the hedge test, and that shows up in months,
  not years.

**For the sleeve as a whole,** showing a Sharpe ratio S is real at two
standard errors takes T ≥ (2/S)² years: 16 at S = 0.5, 4 at S = 1. Judge
the process and the twins, not the P&L.

**The LLM's value,** where a rules engine runs beside it on the same
theme in shadow, is the difference between the two.

## The journal: what E0's schema gains

On top of EXECUTION_PLAN's tables (one migration, D36):

```sql
CREATE TABLE themes (
    id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL,         -- a change is a new version
    name TEXT NOT NULL,
    direction TEXT NOT NULL,          -- your sentence or two
    budget REAL NOT NULL CHECK (budget > 0 AND budget <= 1),  -- ℓ, of K
    instruments TEXT NOT NULL,        -- STK, OPT, FUT, SHORT, comma-separated
    universe TEXT,                    -- NULL: anything allowed
    engine TEXT NOT NULL CHECK (engine IN ('rules', 'llm')),
    experiment_id INTEGER,            -- research_runs.id, for a rules engine
    twin TEXT NOT NULL,               -- the passive expression
    end_condition TEXT,               -- NULL: until you end it
    opened_at TEXT NOT NULL,          -- UTC
    ended_at TEXT,
    end_reason TEXT,
    UNIQUE (name, version)
);

CREATE TABLE llm_decisions (
    id INTEGER PRIMARY KEY,
    theme_id INTEGER NOT NULL REFERENCES themes(id),
    at TEXT NOT NULL,                 -- UTC
    model TEXT NOT NULL,
    inputs TEXT NOT NULL,             -- everything it was shown, as sent
    response TEXT NOT NULL,           -- as received
    outcome TEXT NOT NULL             -- shadow | accepted | rejected: <check>
);

CREATE TABLE digests (
    id INTEGER PRIMARY KEY,
    week_ending TEXT NOT NULL UNIQUE, -- a session date (D19)
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

`order_intents` gains:
- `theme_id`
- `sec_type`, `multiplier`, `right`, `strike`, `expiry`
- `stress_loss`
- `llm_decision_id`, for a decision the LLM proposed

`order_ref` becomes `qh:<theme id>:<session date>:<run>:<contract id>`,
where the run is `w` (weekly) or `d` (daily risk). It stays deterministic,
so a rerun finds its own orders at the broker.

## What stays from D52

- One guarded order path: `broker/orders.py`, called only by
  `services/execution.py`, enforced by `tests/test_layering.py`.
- Off unless the settings enable it, and paper unless a live decision and
  the live account both match.
- Journal before sending, never send twice, nothing reconnects or retries
  a send.
- Limits outside the strategy, the loss stops, and the kill switch.
- A dedicated paper account, then a dedicated live account.
- No login automation and no stored IBKR credentials.
- I build and test against the simulated exchange. You run paper, and you
  decide what goes live.

## What changes, and the conflicts

1. **D52's scope.**
   - Was: US stocks and ETFs, long or cash.
   - Becomes: any instrument a theme allows, within the stress and size
     rules. Shorts, options with capped loss, and futures once K allows.
2. **Hard rule 1's last clause.** "Every deployed strategy is a saved
   Research run" becomes: every rules engine is a saved Research run, and
   every LLM decision is journaled in full before its proposal is
   checked.
3. **Approval modes** are new: `each`, then `digest` by your switch.
4. **Dependencies (S5).**
   - The LLM is a network call: a provider client in `providers/`, which
     already knows only `data/` and the standard library, as the SEC
     client does.
   - Standard-library HTTP or the provider's own SDK is S5's decision.
   - The API key comes from an environment variable. It's never stored in
     `settings.toml` or the store.
   - The call sends the sleeve's own positions and prices to the
     provider, never the core's. That's yours to accept.
5. **Research** gains shorts and multi-asset universes (S2).
   - The self-financing accounting in `models/backtest.py` must be
     re-derived for short proceeds and borrow costs before any code is
     written.
6. **Pages: one job each.**
   - The Orders page (E2) is the plumbing: the ticket, the blotter, the
     journal and the kill switch.
   - The Sleeve page (S1 on) is the dashboard: themes, twins, positions,
     budgets and the digest.

## Milestones

E0 and E1 are done (D52, D54). E1's live check is still yours.

- **E2. Orders on paper, by hand** (as planned), with one change:
  - The order types in `data/orders.py` cover every instrument type from
    the start: security type, multiplier, strike, expiry, right. Options
    and futures then don't force a rewrite.
  - The ticket also allows short sales, on paper.
- **E3. Reconciliation, the crash-at-every-write test, the kill switch
  and the loss stops** (as planned).
- **S1. Themes and twins, with no orders.**
  - Theme records, the Sleeve page, and each twin tracked in shadow from
    stored bars.
  - The digest, holding twins only.
  - You start grading your ideas before any machine trades them.
  - It touches different files from E2 and E3, so it can run alongside
    them.
- **S2. The first rules engine, in shadow** (was E4).
  - Research learns shorts and a cross-asset universe, derived first.
  - Tailwinds is the first engine: it decides, sizes, checks and
    journals, and sends nothing.
  - Its decisions must equal the backtest's exactly.
- **S3. The rules engine on paper, approving each order** (was E5).
  - Stress budgets, the gross cap, short stops, and the weekly and daily
    runs.
  - It runs for the weeks you choose.
- **S4. Options on paper.**
  - Contracts in the ticket and as expressions.
  - Day limit orders and their life cycle.
  - Expiry and assignment handling, and the stress loss of spreads.
- **S5. The LLM engine.**
  - First in shadow: proposals journaled, never sent, and graded against
    the twin and a rules engine on the same theme.
  - Then on paper, approving each order.
  - Its own decision entry: the provider, the dependency, the key, and
    what data it may see.
- **S6. Acting alone, on paper.** `digest` mode, for the weeks you
  choose.
- **S7. Live** (was E6).
  - Its own decision entry, a dedicated live account and a small K.
  - `each` at first, then `digest` when you switch it on.
- **Later, when K allows: futures.** Contract rolls, notice dates and
  margin, as their own milestone.

## Decisions for you

1. **Approve the reframe,** the amendment of D52 (scope, engines,
   approval), and S1–S7 in place of E4–E6.
2. **K,** and K = min(setting, net liquidation) so that losses shrink it.
3. **The proposed limits:**
   - s_long 0.5 and s_short 1.0
   - G 1.5
   - each theme's ℓ
   - a sleeve drawdown stop (25% proposed)
   - EXECUTION_PLAN's daily stop (5%)
4. **The first theme:** Tailwinds, a rules engine, is proposed, along
   with its universe. A first LLM theme waits for S5.
5. **The cadence:** a weekly decision and a daily risk run.
6. **The LLM, at S5:** the provider, the model, the dependency, the key,
   and what data it sees.
7. **Paper lengths** for S3 and S6, in weeks.
8. **S1 alongside E2–E3** (proposed), or after them.

## Doc changes on approval

- **DECISIONS.md:** a new entry amending D52, and each milestone's own as
  it lands.
- **AGENTS.md and CLAUDE.md:** hard rule 1's last clause (item 2 above).
- **EXECUTION_PLAN.md:** a status note. E4–E6 are replaced by S2–S7, and
  the settings and journal are extended here.
- **ROADMAP.md, ARCHITECTURE.md and CURRENT_STATE.md,** as milestones
  land.

## Sources

Papers:
- Ariel (1987), "A monthly effect in stock returns", *Journal of
  Financial Economics*.
- Lakonishok and Smidt (1988), "Are seasonal anomalies real? A ninety-year
  perspective", *Review of Financial Studies*.
- Grinold (1989), "The fundamental law of active management", *Journal of
  Portfolio Management*.
- McConnell and Xu (2008), "Equity returns at the turn of the month",
  *Financial Analysts Journal*.
- Moskowitz, Ooi and Pedersen (2012), "Time series momentum", *Journal of
  Financial Economics*.
- McLean and Pontiff (2016), "Does academic research destroy stock return
  predictability?", *Journal of Finance*.
- Hurst, Ooi and Pedersen (2017), "A century of evidence on
  trend-following investing", *Journal of Portfolio Management*.

IBKR and CME facts, checked 2026-10-29:
- [IBKR: minimum deposit](https://www.interactivebrokers.ca/html/retailAccount/minimumDeposit.html)
  and [margin below USD 2,000](https://ibkb.interactivebrokers.com/es/node/3105)
- [IBKR: options commissions](https://www.interactivebrokers.com/en/pricing/commissions-options.php)
- [CME: Micro Bitcoin futures](https://www.cmegroup.com/education/lessons/micro-bitcoin-futures-product-overview.html)
