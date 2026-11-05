# HEDGE_PLAN.md — a hedge lab: puts, collars, trend and cash, on your book

Proposal, 2026-11-03. Planning only: no app code changes. Each milestone
needs your approval before it starts, as with the other plans, and **do
not build ahead** (AGENTS.md hard rule 5) still holds. None of your
holdings, values or rule settings appear here.

## What you asked

You picked the hedge and crash lab from the gaps against the Quant Guild
Library (lectures 126, 133 and 138: tail risk, crisis alpha, protecting a
portfolio against a crash): puts, collars and trend, compared on your
book.

It answers a question your creed has left open since 2026-10-24. The
hedge sleeve is undecided ("probably just cash, a T-bill ETF?"), and a
Treasury fund already failed the test once: it fell with stocks in 2022.

The lab doesn't pick for you. It puts every candidate on one page with the
same yardstick: what the hedge costs in normal times, what it paid in each
real crash, and what it did to long-run growth. You choose.

## What the code does today, and why that isn't enough

- **`models/growth.py` (W3)** sizes a hedge you already hold: the
  minimum-variance size c_mv, the Kelly-shifted size c*, and the hedge
  budget as annual theta. It works on the replay of today's holdings
  over the lookback, about two years.
- **`models/portfolio.py`** reprices options in that replay, but **holds
  volatility at the underlying's realized vol, with no smile.** A put's
  crash payoff comes largely from the volatility spike, so the replay
  understates exactly what a hedge is for (the numbers are below).
- **ALLOCATION_PLAN A2** (proposed) replays a constant-β book through the
  benchmark's stored crashes. Its hedges are linear: less β, more cash.
- **The Options Lab** prices a protective put today off your fitted
  surface, one position at a time.

What's missing:
- candidates you don't hold yet
- option hedges that roll, over decades that contain real crashes
- implied vol that moves with the market
- one table comparing them all on your book

## The worked numbers (illustrative)

Black–Scholes from `models/black_scholes.py`, S = 100, r = 4%, a dividend
yield of 1.3%, and an assumed smile: +3, +6 and +11 vol points at 5%, 10%
and 20% out of the money. The cost is the premium, before anything the
put pays back.

| Put | Calm (ATM 15%) | Stressed (ATM 30%) |
|---|---|---|
| 5% OTM, 1 month, rolled | 4.7% a year | 20.0% a year |
| 10% OTM, 3 months, rolled | 2.9% | 11.0% |
| 10% OTM, 1 year | 3.1% | 8.0% |
| 20% OTM, 3 months, rolled | 0.7% | 4.8% |

What these say:
- **Protection bought after the spike costs about four times as much.**
  The price of a put is mostly the price of volatility.
- **A zero-cost collar** on the 3-month 10%-OTM put sells a call about
  6.5% above spot. That caps each quarter's gain at about 6.5%.
- **Vol matters most in the middling falls.** A month after buying that
  put, with the index down 12%, it's worth 5.17 if implied vol has risen
  to 30%, and 3.90 if vol is held at 21% as today's replay does: the
  replay is about a third short. After a 30% fall it hardly matters (20.16
  against 19.56), because the put is deep in the money.
- **At a small book, granularity binds.** One index-ETF contract covers 100
  shares, which can be more than the whole amount you'd hedge. The lab
  shows the hedge ratio whole contracts allow, beside the ideal one.

## The math

The derivation goes in `models/hedge.py`'s docstring and is reviewed
before the code, as with every quant module (hard rule 4).

### 1. The book and what's hedged

- V is the book's value, b its β to the index (W2, with its SE), and
  h ∈ [0, 1] the hedge ratio. The hedged notional is N = h·b·V.
- **Two books are run, both shown:**
  - **Today's mix**, over its own lookback: Portfolio's replay, exact but
    short. It rarely contains a crash.
  - **The β proxy**, over the longest history: A2's
    Rₜ = rₜ + b·(R_m,t − rₜ). It contains every crash since the data
    starts. It's a floor: single stocks add their own risk, and β tends to
    rise in a crash.

### 2. Rolling option programs

At each roll date τ (every Δ days), buy n puts struck at K = m·S_τ,
expiring at τ + T:

    n = h·b·V_τ / (100·S_τ)        (ideal; whole contracts round it)

- **Daily marks.** Each open contract is marked at Black–Scholes with
  the day's implied vol σₜ(k, τ'), k = ln(K/F) and τ' the time left.
  Expiry settles at intrinsic value.
- **Self-financing.** Premiums come out of the book's cash. P&L is the
  change in marks plus settlements, minus premiums and costs.
- **Costs.** Each trade pays a half-spread c as a fraction of the premium
  (decision 6).
- **Collar:** also sell a call at K_c, chosen so that C(K_c) = P(K). The
  call's price falls continuously in K_c, so bisection finds it.
- **Put spread:** also sell a put at K₂ < K. This caps the protection at
  K − K₂ and cuts the premium.

### 3. Implied vol through history

Traded option prices from 1993 aren't in reach, so the marks are model
prices:

    σₜ(k, T) = VIXₜ · ρ · s(k, T)

- **VIXₜ:** the 30-day index level, from 1990.
- **ρ = σ_ATM / VIX,** measured on your own surface today: the ATM
  30-day IV over its model-free variance strike (VARIANCE_PLAN I1).
- **s(k, T):** today's smile and term shape, normalized to 1 at the
  30-day ATM point (decision 5).

**The assumption to keep in view:** one shape for 30 years. In a crash
the term structure inverts, so a fixed shape understates short-dated put
marks and overstates long-dated ones. The lab also runs a flat smile
(s ≡ 1) and shows both.

### 4. The linear and rule-based candidates

- **Cash or T-bills:** move a weight w from the book to cash:
  R = (1 − w)·R_B + w·r.
- **Treasuries:** a stored bond ETF's returns as the stream. Its hedge
  depends on the stock–bond correlation, so the lab shows that by era.
  2022 is the case where it was positive.
- **Trend on the index:** xₜ = 1 when the close is above its 200-day
  average (or the 12-month excess return is positive), decided at close t
  and held t → t+1, the backtester's timing (b). Then
  R = xₜ·R_m + (1 − xₜ)·r. This reuses Research's Momentum rule.
- **Your own trend fund:** its returns over its own (short) history, and
  their conditional β.
- **Vol targeting:** wₜ = min(1, σ*/σ̂ₜ), with σ̂ₜ from GARCH(1,1)
  (`models/garch.py`) or 21-day realized vol, and σ* the book's long-run
  vol so it never levers. This is Moreira–Muir, and TARGET_PLAN's vol
  layer.

### 5. One yardstick

For each candidate at each size:
- **Cost:** the annualized mean of R_hedged − R_book on days outside crash
  windows. It's what you pay while nothing happens.
- **The crash table:** each window where the index fell at least 15% from
  peak to trough is found by rule, not chosen by hand. Since 1993 that's
  about nine: 1998, 2000–02, 2007–09, 2010, 2011, 2018, 2020, 2022 and
  2025. For each, the book's loss hedged and unhedged, and what the hedge
  paid.
- **Δ log growth, with its error.** Take the paired daily difference
  dₜ = ln(1 + R_hedged,t) − ln(1 + R_book,t). Then Δg = 252·mean(d), with a
  Newey–West SE. Pairing removes the market noise both sides share, so
  the error is far smaller than either growth rate's own.
- **Drawdown:** the deepest drawdown and the worst 12 months, hedged and
  not.
- **Convexity:** monthly up and down betas of the hedge's return on the
  index (Henriksson–Merton). A hedge earns its name when β_down is below
  β_up.
- **Granularity:** h_eff = 100·S·n_whole / (b·V), the ratio whole
  contracts actually give.

**The frontier** plots cost per year against the average crash loss
saved, one dot per candidate and size. Dominated dots are dimmed.
Nothing is optimized.

### 6. Today

- **Each option candidate priced off your surface now**
  (`MarketSurface.point(K, dte)`), as a cost per year, with its
  percentile among the same strike and tenor's VIX-based prices since 1993.
  This answers "is protection cheap or dear right now".
- **Trend and vol targeting:** today's signal (in or out) and today's
  scale.

### 7. What it can't say

- **About nine crashes since 1993.** Averages rest on few episodes, so the
  table shows each crash first, and a hedge that worked in 2008 but not in
  2020 is visible as such.
- **Historical option prices are model prices** from VIX and one smile
  shape, not prices anyone traded.
- **The β proxy is a floor** for a book of stocks.
- **Selection.** Many candidates and sizes go through one window. The one
  you pick will look better than it will turn out to be (McLean–Pontiff;
  the deflated Sharpe idea). The frontier shows them all for this reason.
- **Left out:**
  - gaps between closes
  - early exercise of American ETF puts (small out of the money;
    HORIZON X1 is the lattice)
  - VIX calls (no history in reach)

## The plan: U1 → U5

U for umbrella; H is HORIZON's Track H.

### U1. The hedge math (`models/hedge.py`)

**Status:** landed 2026-11-04 (D68): the linear and rule candidates of
§4, the rolled option programs of §2–3 (puts, put spreads, collars), and
the yardstick of §5. The smile function and VIX history come with U2–U3.

- Option programs (puts, put spreads, collars), rolled and marked daily
  as in §2–3.
- The linear and rule candidates of §4, reusing `strategies.py` and
  `garch.py`.
- The yardstick of §5.
- Pure, with validated inputs.

**Tests:**
- flat vol and no skew reproduce Black–Scholes marks
- a collar's call strike makes the net premium zero (parity check)
- in a planted crash world, the put program's payoff matches intrinsic
- zero hedge ratio gives the unhedged book exactly
- crash losses fall monotonically in h for puts
- contract rounding and h_eff
- Δg's sign in worlds where the answer is known
- crash windows found by rule on a synthetic path

### U2. The data

**Status:** landed 2026-11-04 (D69), not yet checked live. FRED's
VIXCLS, DTB3 and DGS3MO come with `[fred] key`. IBKR's SPX and VIX
history come by Ctrl+K **Sync index history from IBKR** with TWS
connected; its log gives the depth.

- **The index, as far back as IBKR serves it,** traded and adjusted. The
  depth is checked first; ALLOCATION A2's decision 3 asks the same
  question.
- **VIX history** (decision 3):
  - VARIANCE_PLAN I3's CBOE provider (terms check, D34)
  - or IBKR index bars through the existing index contract path
  - stored as fetched, with provenance, and offline-first (V0)
- **The cash rate:** FRED's T-bill (LENS W2b) when it exists, else a
  constant stated on the page.

**Tests:** the provider parses its fixture; offline reads; provenance.

### U3. The service

**Status:** landed 2026-11-04 (D70). One change to decision 4, for you
to overrule: the option programs run on French's total-return level, so
IBKR's SPX history is a cross-check rather than the underlying.

- `services/hedge.py` builds both books: b from W2, today's mix from
  Portfolio.
- It runs the candidates on the TaskRunner.
- It reads today's surface for the index from the Volatility Lab's fit.
- **Tests:** equality with Portfolio's replay for h = 0; the surface
  quotes; missing data named, never fetched behind your back.

### U4. The Hedge Lab page

A dock (D55) with cards on the grid (D56):
- **The book:** b ± SE, the target (whole book or core), the window, and
  the sizes.
- **The frontier:** cost per year against crash loss saved, with hover
  readouts.
- **The crash table:** one row per window, one column per candidate.
- **Equity curves:** unhedged against the selected candidate.
- **The candidate's card:** convexity, Δg ± SE, cost, h_eff, and today's
  price with its percentile.

**Tests:** page geometry and a smoke test, per AGENTS.md.

### U5. Hooks into the rest (each only if you want it)

- **ALLOCATION A3:** a What-if vector can carry a hedge.
- **REGIME_MODEL_PLAN:** the hedge mix as a function of the regime reads
  each candidate's payoff by regime.
- **Portfolio's replay:** marks held options with the §3 vols instead of
  realized vol, which fixes the limitation above.
- **REGIME.md:** a hedge-sleeve row, if you decide to write one. The lab
  never grades you against it.

## Relation to the other plans

- **ALLOCATION_PLAN A2/A3:** it shares A2's β replay and A2's data
  question. U4 is the hedge-side twin of A3's What-if.
- **VARIANCE_PLAN:** the variance premium is the expected cost of an
  index-put hedge. U's realized put cost is its empirical counterpart. I1
  gives ρ, and I3 gives VIX history.
- **TARGET_PLAN and REGIME_MODEL_PLAN:** vol targeting is T2's layer as a
  candidate. Your 2026-10-24 tests on a century of index data found it
  positive, but only borderline once the many rules tried were accounted
  for.
- **SLEEVE_PLAN:** its Tailwinds trend sleeve is a hedge candidate too.
- **EXECUTION_PLAN (D52):** nothing here sends orders.
- **LITERATURE_PLAN:** U1 fills hollow dots on the timeline:
  - Coval–Shumway 2001 (expected put returns, measured)
  - Carr–Wu 2009 (the premium)
  - Moreira–Muir 2017 (vol targeting)
  - Longin–Solnik 2001 (correlations in crashes)

  Candidates for the canon, verified before they're added:
  - Henriksson & Merton (1981), up and down betas
  - Treynor & Mazuy (1966), quadratic timing
  - Fung & Hsieh (2001), trend following as a lookback straddle
  - Hurst, Ooi & Pedersen (2017), a century of trend following

## Fit with the hard rules

- **Read-only, no orders, no new dependency.** Bisection and
  Black–Scholes are already here.
- **Data:** a new provider only through VARIANCE I3's terms check (D34),
  or IBKR's existing index path.
- **Layering:**
  - the math in `models/hedge.py`
  - the books and the job in `services/hedge.py`
  - the page in `gui/pages/`
- **Privacy:** V, your holdings and any hedge row stay in your local store
  and private REGIME.md. Nothing here carries a value.

## Decisions for you

1. **Settled 2026-11-03 (D59):** approved as the next track, after ALLOCATION A1–A2. Merging PR #21 is still yours. The question was: **Approve the plan,** and its order against VARIANCE I1–I3,
   ALLOCATION A2–A3, REGIME_MODEL Q1 and EXECUTION E2. U1 needs no new
   data, so it can start alone.
2. **Settled 2026-11-03 (D59):** the proposed v1 list (you had no preference). The question was: **The candidates in v1:**
   - puts at 5, 10 and 20% out of the money, over 1, 3 and 12 months
   - put spreads and zero-cost collars
   - index trend
   - vol targeting
   - Treasuries and cash
   - your trend fund

   VIX calls wait for data.
3. **Settled 2026-11-03 (D59):** CBOE's VIX history, through VARIANCE I3's provider (approved). **Changed by D60:** Cboe's terms refuse storing the data, so VIX comes from FRED's `VIXCLS` (or IBKR's index bars). The question was: **The VIX source:** VARIANCE I3's CBOE provider, IBKR index bars, or
   both.
4. **Settled 2026-11-03 (D59):** IBKR's index as deep as it goes for the option candidates, and Ken French's series for the linear ones. The question was: **The index history:** IBKR as deep as it goes (checked first), or a
   longer series without strikes for the linear candidates only.
5. **Settled 2026-11-03 (D59):** today's shape held fixed, with the flat smile beside it (proposed; you had no preference). The question was: **The smile through history:** today's shape held fixed (proposed),
   with the flat smile beside it, or the flat smile only.
6. **Settled 2026-11-03 (D59):** today's half-spreads (proposed; you had no preference). The question was: **Option costs:** today's half-spreads from your chain applied
   throughout (proposed), or a fixed share of the premium.
7. **Settled 2026-11-03 (D59):** Δ log growth first, with your drawdown limit as the line (proposed; you had no preference). The question was: **The yardstick's order:** Δ log growth first, with your drawdown
   limit as the line to beat (proposed, matching your CAGR thesis), or
   Sharpe or Calmar.
8. **Settled 2026-11-03 (D59):** the whole book's β (proposed; you had no preference). The question was: **What's hedged:** the whole book's β, or the core only.
9. **Settled 2026-11-03 (D59):** its own Hedge Lab dock (proposed; you had no preference). The question was: **Where it lives:** its own Hedge Lab dock (proposed), or a tab beside
   A3's What-if.

## Doc changes on approval

- **ROADMAP.md:** the hedge track, U1 → U5.
- **DECISIONS.md:** an entry for the track, numbered at approval (D58 is
  the latest), and entries for the vol assumption and the yardstick as
  their milestones land.
- **ARCHITECTURE.md:** the new modules and page.
- **CURRENT_STATE.md:** each milestone as it lands.
- **The literature canon:** the papers above, verified.
