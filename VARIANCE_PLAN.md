# VARIANCE_PLAN.md — what variance costs, and the premium over what it delivers

Proposal, 2026-11-03. Planning only: no app code changes. Each milestone
needs your approval before it starts, as with the other plans.

## What you asked

You raised a Carr–Madan options calculator. That name covers two results:
- **Carr & Madan (1998), spanning:** any payoff is a portfolio of puts and
  calls.
- **Carr & Madan (1999), FFT pricing:** prices from a characteristic
  function.

> "Variance/VRP first"

So this plan builds the 1998 result into the price of variance read off
your chains, and the variance risk premium (VRP): that price against the
variance that GARCH expects and the variance that follows. FFT pricing and
Heston calibration come after, as their own plan.

**Why it's useful to you as a trader:**
- **Index puts cost the premium.** A put hedge pays it. A sold put or
  call earns it.
- **The lab compares the wrong number today.** The Volatility Lab's Term
  structure view sets ATM IV beside GARCH's expected vol. But ATM IV isn't
  the price of expected variance; the variance strike is. Under a skew the
  variance strike sits well above ATM, so today's view understates the
  premium.

## The worked example (a synthetic smile)

There's no live chain behind these numbers: your store has no chain
snapshots today (checked read-only, 2026-11-03). The smile is an
equity-like 30-day SSVI slice: ATM 14.0%, ρ = −0.7, η = 1.0, γ = ½, so
∂σ/∂k ≈ −1.2 at the money. The scratch checks behind every figure here
are reproduced as I1's tests.

| Quantity | Value |
|---|---|
| ATM implied vol | 14.0% |
| √(variance strike), the whole smile | 16.6% |
| Same, today's request band only (−2.5 to +1.5 ATM sd) | 15.4%: the band holds 86% of the variance |
| Band −4 to +2 sd | 16.1% (95%) |
| Band −6 to +3 sd | 16.4% (98.5%) |
| VIX-style sum, 5-point strikes over −6 to +3 sd | 16.5% |
| A steeper skew (η = 1.5, ∂σ/∂k ≈ −1.8) | 19.3% against the same 14.0% ATM; today's band holds 73% |

**Put that smile beside a GARCH month of 13.8%** (the app's reading on
2026-10-28):
- ATM against GARCH shows a 0.2 vol point gap.
- The variance strike against GARCH shows 2.8.

The second is the premium a variance seller is paid.

## The math

The derivation goes in the module docstring for review before any code,
as W2, W3 and B1 did.

### 1. Spanning, and the variance strike

For any twice-differentiable payoff f and any level F (Carr & Madan 1998):

    f(S_T) = f(F) + f′(F)(S_T − F)
             + ∫₀^F f″(K)(K − S_T)⁺ dK + ∫_F^∞ f″(K)(S_T − K)⁺ dK

Take f = ln and F the forward, so E^Q[S_T] = F. With P and C the quoted
put and call prices and D the discount factor (both from `models/carry.py`
per expiry):

    −E^Q[ln(S_T / F)] = (1/D) [ ∫₀^F P(K)/K² dK + ∫_F^∞ C(K)/K² dK ]

If the price moves continuously (dS/S = (r − q)dt + σ_t dW), Itô gives
ln(S_T/F) = −½∫σ²dt + ∫σ dW, so the risk-neutral expected integrated
variance is

    K_var · T  ≡  E^Q[∫₀^T σ_t² dt]  =  (2/D) [ ∫₀^F P/K² dK + ∫_F^∞ C/K² dK ]

With D = e^{−rT} this is the VIX's 2e^{rT}/T. No model of σ_t is assumed:
only continuity, and that the quotes are European.

### 2. In the smile's own units

Write k = ln(K/F) and b(k, w) for the Black price of the out-of-the-money
option in units of D·F (the call for k ≥ 0, the put below). Then dK = F e^k
dk, and F and D cancel:

    K_var · T = 2 ∫ e^{−k} b(k, w(k)) dk

Only the total-variance smile w(k) enters. So the estimator integrates each
fitted SVI slice on a fine grid, and the fit's weights (spread and vega)
carry over unchanged.

**An equivalent form** (Gatheral 2006; Fukasawa 2012), with z = −d₂ =
k/√w + √w/2:

    K_var · T = ∫ φ(z) w(k(z)) dz

The variance strike is the smile's total variance averaged under a
standard normal in z. A flat smile gives σ²T, and a skew gives more than
the ATM variance. z must rise with k, which absence of butterfly arbitrage
guarantees. On the example slice the two forms agree to 2 parts in 10⁷
(quadrature error), and the second is the test of the first.

### 3. From the quotes directly (the VIX way)

K₀ is the first listed strike at or below F. Puts are taken below K₀,
calls above it, and their average at K₀:

    K_var · T ≈ (2/D) Σ ΔK_i / K_i² · Q(K_i) − (F/K₀ − 1)²

**The last term.** Between K₀ and F, the sum uses calls where the formula
wants puts. Parity (C − P = D(F − K)) makes the excess 2[F/K₀ − 1 −
ln(F/K₀)] ≈ (F/K₀ − 1)².

**A band from the quotes alone.** Every weight is positive. So the sum at
bids and the sum at asks bound the quoted range's contribution, with no
model.

**The two estimators check each other.** The SVI integral over the quoted
range should lie inside that band, and is flagged when it doesn't.

### 4. Where the answer comes from: quoted range and wings

**Truncation.** Quotes stop somewhere, and the missing wings only ever
lower the sum. The example shows how much: today's request band holds 86%
of the variance at a typical skew and 73% at a steep one. Stress steepens
skews, so the miss is worst when the number matters most.

**The estimator splits K_var into two parts:**
- the quoted range, between the smile's quoted k_min and k_max
- the wings, SVI's linear wings extrapolated beyond them, within Lee's
  bounds

The extrapolated share is shown beside the number, as `market_surface.py`
flags extrapolated points. Decision 2 is how wide to request.

### 5. What the number is not: jumps

With jumps J the log contract and realized variance part ways:

    −2 E^Q[ln(S_T/F)] = E[∫σ²dt] + 2 E[Σ (e^J − 1 − J)]
    E^Q[realized variance] = E[∫σ²dt] + E[Σ J²]

Per jump, 2(e^J − 1 − J)/J² is 0.984 at −5%, 0.967 at −10% and 0.937 at
−20%. So in a crash-prone market the replicated strike sits a little below
a true variance swap rate:
- **Merton example** (σ 12%, half a jump a year, mean −8%, sd 10%, 91
  days): 14.88% against 15.03%.
- **What it's called.** The code says "model-free implied variance"
  (Britten-Jones & Neuberger 2000), never "variance swap rate."
- **How the error leans.** It slightly understates the premium.

### 6. Across expiries

- **Constant maturity:** total variance K_var·T linear in T between the
  two expiries that bracket a tenor. That's the VIX's rule and
  `surface_mesh`'s, and it's exact for variance, which adds over time. It
  never extrapolates in T.
- **Forward variance between expiries:** (K₂T₂ − K₁T₁)/(T₂ − T₁). It must
  be non-negative: the calendar check, in variance terms.

### 7. The realized side

**Realized total variance** over the window (t, t + T]:

    RV = Σ r_i²,  r_i = ln(C_i / C_{i−1})

- Close-to-close, from adjusted closes (D38), with zero mean, as variance
  swaps settle. The bars in the window are counted, not estimated.
- **Compare totals, then annualize both by the same 1/T** (T in calendar
  years, as the chain's dte is). Never 252 against 365.
- **D57.** A window that touches a named split-sized gap is named and left
  out of every average, never repaired.

**GARCH's expectation over the window:**

    E_t[RV] = n · average_variance_forecast(n),  n = trading days in the window

`vol_context.py` approximates n as days × 252/365. Over a month that can
be a day off: about 5% of the variance, or 0.4 vol points at 16%, which
is the same size as what's being measured. Decision 6.

### 8. The premium, three ways

All computed and averaged in variance, which is additive; shown in vol
points (√ of each side) for reading.

| Premium | Definition | Known at |
|---|---|---|
| Against GARCH | K_var(t, T) − E_t^GARCH[RV]/T | t |
| Against trailing realized (Bollerslev, Tauchen & Zhou 2009) | K_var(t, 30d) − the last 21 days' RV, annualized | t, with no model |
| Ex post | K_var(t, T) − RV(t, t+T)/T | t + T: what a short variance position earned |

**The noise is large.**
- **One window.** A 21-day realized variance has a relative standard
  error of about √(2/21) ≈ 31% even with Gaussian returns, and fat tails
  make it worse. One month's ex-post premium says almost nothing.
- **The mean over many windows** does, with Newey–West errors for the
  overlap.
- **A year of daily snapshots** is roughly a dozen independent months: a
  standard error near ±1 vol point before tails. That's why I3 offers VIX
  history.

**Single names differ.**
- Carr & Wu (2009) find the premium large and reliably negative for the
  S&P 500, and smaller and more mixed for single stocks.
- Driessen, Maenhout & Vilkov (2009) find single-stock options carry
  little of it, and trace the index premium largely to correlation risk.

So the card reports a single name's number and claims nothing more.
Single-name chains are also American:
- The fit uses out-of-the-money quotes only.
- An out-of-the-money put's early-exercise value is small, but not zero.

## Fit with the hard rules

- **No orders.** The card informs; nothing gates on it. The execution path
  (D52) doesn't read it unless a later decision says so.
- **No new dependency.** numpy only. Merton prices appear only in tests,
  as a series of Black prices.
- **Data sources:**
  - IBKR chains through the existing `OptionChainSource` port (D34), saved
    by the chain store (D33)
  - CBOE's VIX history only if decision 5 says so: a new provider, with a
    terms check (D34)
- **Layering:**
  - `models/` holds the math, knowing only `data/`
  - `services/` wires it
  - the GUI shows it in a dock (D55/D56), built from `gui/components.py`
    and `gui/charts.py`
- **Threading:** fetches and fits run on the TaskRunner, as the
  Volatility Lab's job does today.

## The plan: I1 → I4

Each milestone is a few files, reviewed before the next. **I1 and I2 run
offline** on synthetic and stored chains. No prerequisite, except that I2
reads D57's `split_gaps` (PR #18).

### I1. Model-free implied variance
`models/implied_variance.py`
- **From a `SurfaceFit`:** each slice's K_var, split into the quoted range
  and the wings, with the extrapolated share.
- **From the quotes:** the VIX-style sum with the K₀ term, at mid, bid
  and ask. That gives the band, and the check that the fit lies inside it.
- **Across expiries:**
  - constant-maturity tenors (30 days, and the request's targets)
  - forward variances, with the calendar check
- **Tests, against planted truths:**
  - a flat smile gives σ² exactly
  - the k-form equals the z-form on SVI and SSVI slices
  - on the synthetic SSVI world, the estimate matches the true surface's
    integral within its band
  - on a Merton chain, K_var matches −2E[ln S_T/F]'s closed form to 10⁻⁶,
    and sits below E[QV] by section 5's jump term
  - the VIX sum converges as strikes densify, and truncation only lowers
    it
  - interpolation is exact on additive variance
  - inputs are validated
- **A live check,** with TWS running: SPX and SPY. Record the
  extrapolated share at today's band and at a wide one; it decides
  decision 2.

### I2. The realized side and the premium
`models/variance_premium.py`, wired through `services/vol_context.py`
- Realized total variance over a window, GARCH's expected total, the
  trailing 21-day realized.
- **The three premiums**, in variance, shown in vol points.
- **The ex-post series** over completed windows, its mean ± Newey–West SE,
  and today's percentile.
- **Tests:**
  - **GBM world:** the chain is priced at the paths' own σ. The mean
    premium is zero within its SE (|z| < 3).
  - **A planted premium** (the chain priced at 1.15σ) is recovered.
  - **No look-ahead:** an ex-post value appears only after t + T, and
    appending bars never changes a past value.
  - **Trading-day counting** is correct.
  - **D57:** a window with a named gap is excluded and named.

### I3. History
- **A variance chain request** (decision 2): a wider band and more strikes,
  in the same store and with the same parity carry. Fetch time is measured
  first; today a symbol takes about 42 s.
- **How snapshots accumulate** (decision 4): one per symbol per trading
  day, idempotent.
- **VIX history, optional** (decision 5):
  - CBOE's daily closes since 1990, with SPX realized variance, give the
    30-day premium a 36-year history.
  - VIX is the same quantity, computed on SPX with its own strike rules,
    and the card says which source a point came from.
  - That history is what REGIME_MODEL_PLAN Q3 would test.
- **Tests:**
  - a second snapshot on the same day is a no-op
  - the provider parses its fixture
  - VIX points and own points never mix in one average without a label

### I4. The card
- **The Term structure view** gains √K_var per expiry, with its quote band,
  beside ATM IV, GARCH and realized. Points that are mostly extrapolated are
  drawn hollow.
- **A Variance premium dock:**
  - **Per expiry:**
    - √K_var and ATM IV
    - GARCH vol and trailing realized
    - the premium against each
    - the extrapolated share
  - **Headline:** the 30-day constant-maturity tenor.
- **History, once windows exist:**
  - the implied 30 days against the realized that followed
  - the running mean premium ± SE
  - today's percentile
- **Tests:** offline GUI smoke tests on a synthetic and a stored chain.

## Relation to the other plans

- **REGIME_MODEL_PLAN:** the 30-day premium joins Q1's table as a
  candidate input.
  - **The evidence so far:** Bollerslev, Tauchen & Zhou (2009) found it
    predicted quarterly index returns in-sample.
  - **What it must clear:** Q3's out-of-sample, family-wise bar, like any
    input.
  - **What it needs:** I3's history.
- **BUDGET_PLAN:** the premium is the expected cost of a hedge stream
  bought in index options. A later B milestone could read it; nothing here
  depends on that.
- **LITERATURE_PLAN (PR #19):** these papers join the canon, `lives_in`
  the two new modules. Each is verified before it's canon.
  - Carr & Madan 1998
  - Demeterfi, Derman, Kamal & Zou 1999
  - Britten-Jones & Neuberger 2000
  - Jiang & Tian 2005, on truncation and discretization
  - Carr & Wu 2009
  - Bollerslev, Tauchen & Zhou 2009
  - Driessen, Maenhout & Vilkov 2009
  - Fukasawa 2012
  - CBOE's VIX white paper
- **The FFT / Heston plan** (Carr & Madan 1999) follows. I1's Merton chain
  becomes its first characteristic-function check.
- **Not in this plan:**
  - Risk-neutral skew and kurtosis (Bakshi, Kapadia & Madan 2003). They
    are the same integrals with other weights: a possible I5.
  - Any trading rule.

## Decisions for you

1. **Settled 2026-11-03 (D59):** after the hedge track. I3's CBOE provider comes earlier, in the data batch, because HEDGE U2 needs it. The question was: **Order.** I1–I2 are small and offline. Where do they go against
   REGIME Q1, ALLOCATION A1–A4, BUDGET B1–B3 and EXECUTION E2?
2. **Settled 2026-11-03 (D59):** wide: −6 to +3 ATM sd, about 41 strikes, with fetch time measured first. The question was: **The variance request:**
   - a band of −6 to +3 ATM sd with about 41 strikes (fetch time measured
     first)
   - or today's band, with about 14% of the variance extrapolated at a
     typical skew, more in stress
3. **Settled 2026-11-03 (D59):** SPX, with SPY bars for realized. It's the VIX's own underlying, so today's ρ lines up with 30 years of VIX. The question was: **The underlying:**
   - SPX (European, cash-settled, the VIX's own), with SPY bars for
     realized
   - or SPY for both
4. **Settled 2026-11-03 (D59):** daily. The first time the app runs with TWS connected each trading day, it snapshots a watchlist you set, on the TaskRunner. The question was: **How snapshots accumulate:**
   - a button
   - the first Volatility Lab visit of each trading day
   - or a headless job while TWS runs
5. **Settled 2026-11-03 (D59):** yes, a CBOE provider after its terms check. **Changed by D60:** Cboe's terms refuse storing the data, so VIX comes from FRED's `VIXCLS` (or IBKR's index bars). The question was: **VIX history from CBOE:** a new provider with a terms check, or your
   own snapshots only.
6. **Settled 2026-11-03 (D59):** a small NYSE holiday rule set in `data/`, tested. The question was: **Trading days:**
   - a small NYSE holiday rule set in `data/`, tested
   - or weekdays only (up to a day per month, about 0.4 vol points)
7. **Settled 2026-11-03 (D59):** every expiry, with 30 days as the headline. The question was: **Tenors on the card:** 30 days only, or every target expiry too.

## Doc changes on approval

- **ROADMAP.md:** I1–I4, placed per decision 1.
- **DECISIONS.md:** one entry per milestone, at the next free number (D58
  is PR #19's).
- **CURRENT_STATE.md:** the plan, then each milestone.
- **ARCHITECTURE.md:** the two modules and the data flow.
