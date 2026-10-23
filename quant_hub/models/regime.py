r"""Regime checks: the owner's rules, measured and graded (W7).

**For review: the derivation only, no code yet** (2026-10-22), as W2's and
W3's began. The rules are the owner's ``REGIME.md`` (sections 1–8). This
module answers, at each review:
- Which rules hold, and which are breached, each with its number?
- When a rule reads an estimate, is the breach evidence or noise?
- Has a stream failed long enough to count against the thesis?
- Is a holding's variance one day's doing?
- Is the market's state calm, and what is volatility expected to be?

The checks are pure functions of a rule set and the measures. Everything
around them is engineering, in WORKSTATION_PLAN.md's W7:
- reading the rules from ``REGIME.md`` (``importers/regime_md.py``)
- storing each version (``data/regime.py``, ``storage/regime_store.py``,
  schema v6)
- the review's job chain and journal (``services/review.py``)

Rules and measures
------------------
A **rule** is one row of ``REGIME.md``:
- the measure it reads
- a limit: an upper bound U, a lower bound L, or a band [L, U]
- its unit
- the owner's grade on breach g ∈ {fail, warn, note}

A blank value (``___``) turns the rule off. A value the parser can't read
is a loud error that names the line, never a guess.

A **measure** is (m̂, s): an estimate and its standard error. s is absent
for quantities read exactly from the snapshot or computed from it without
sampling error:
- weights, cash, effective holdings N_eff = 1 / Σ wᵢ²
- premium at risk, short options, days to expiry
- a sleeve's weight against its intended range

The estimated measures come from W2–W6 with their own errors:
- β: Newey–West (W2)
- α, the growth margin, and the sizes c_mv and c*: W3's errors
- the residual correlation ρ: Fisher z, s_z = 1 / √(T − 3) (W2)

Some estimates have no error in the models yet: volatility, Euler risk
shares, VaR, ES and drawdown. They're checked as exact until one is
derived. The review says so beside each.

A breach, and how often noise causes one
----------------------------------------
For an upper limit U and the owner's evidence level k ≥ 0 (REGIME.md's
"by ___ SE"; k = 0 means the estimate alone):

    breach  ⇔  m̂ − k·s > U

When m̂ > U without that, the review shows the rule as *near*: visible,
not graded. A lower limit mirrors this, and a band checks both sides. An
exact measure breaches when m̂ > U.

For a correlation limit |ρ| ≤ U, the same rule applies on Fisher's scale.
With z = atanh ρ̂:

    breach  ⇔  tanh(|z| − k·s_z) > U

How often noise alone breaches: take m̂ ~ N(m, s²) with s known.

    P(breach) = 1 − Φ((U + k·s − m) / s)

At the boundary, m = U, that's 1 − Φ(k):

| k | 0 | 1 | 1.645 | 2 |
|---|---|---|---|---|
| Share of reviews breached when the true value sits on the limit | 50% | 16% | 5% | 2.3% |

A true excess of δ above the limit is caught with probability
1 − Φ(k − δ/s). Raising k trades false breaches for missed ones.

Significance isn't size. At T = 500 days, any |ρ̂| above
1.96 / √497 ≈ 0.088 is statistically nonzero. So a correlation limit
should be a size the owner cares about, with k deciding how sure to be.

Persistence: a delay, not a filter
----------------------------------
Section 1 counts a stream's test as a *fail* after N reviews in a row, and
as a *warn* before that. The run length is counted over reviews, one per
trading day (that day's last):

    R_n = R_{n−1} + 1 if review n breaches, else 0
    warn if 1 ≤ R_n < N;  fail if R_n ≥ N

Consecutive reviews on a lookback of W days share W − 1 of them. For a
mean over the window, consecutive estimates have correlation
(W − 1) / W ≈ 0.998 at W = 500. So N daily reviews add only N days of new
evidence:
- A breach caused by estimation noise persists for months, whatever N is.
  The filter for noise is k.
- What N does filter is a jump: a day entering or leaving the window.
  It is a delay measured in days, so the owner should set N as one.

One day dominating a series
---------------------------
For a holding's daily returns r_t over the window:

    D = max_t r_t² / Σ_t r_t²

D is the largest day's share of the sum of squares, which is the variance
for a mean near zero.
- **A huge day pushes D to 1.** One day of size x, among T − 1 days of
  variance σ², gives D = x² / (x² + (T − 1)σ²). For example, a +1,000%
  day in a stock that otherwise moves 3% a day, with T = 500, gives
  D ≈ 0.996.
- **Variance-based measures are then that day's.** Volatility, risk
  shares, R², Kelly and the growth figures all follow it. Loss-based ones
  (VaR, ES, drawdown) aren't moved by an upside jump, though a crash moves
  both.

What D is under the null, at T = 500 iid days (simulated, 20,000 runs,
seed 7):

| Null | Median | 95% | 99% | 99.9% |
|---|---|---|---|---|
| Gaussian | 0.020 | 0.030 | 0.035 | 0.045 |
| Student t(3), unit variance | 0.101 | 0.374 | 0.641 | 0.893 |

The Gaussian null is far too thin for equities. SPY's own D from
2024-10-24 to 2026-10-21 is 0.20, from one +10.5% day. Eight other
stocks and ETFs measured over that window ran from 0.04 to 0.20.

The flag: D above the t(3) null's 99.9% quantile at the series' length.
That's about 0.89 at T = 500. It's calibrated by a fixed-seed simulation
on a grid of T and tested. A flagged holding's variance-based measures are
shown twice: as measured, and with that day removed. Which of the two is
graded is the owner's choice.

Market-state gates (section 7)
------------------------------
Each review refits the models in ``models/markov.py`` and
``models/garch.py`` to the benchmark's daily log returns over the lookback.
- **Calm-regime probability.** A K-regime Gaussian HMM. The calm regime is
  the one with the lowest variance. The gate reads the *filtered*
  probability α_T(calm) = P(s_T = calm | r_1..r_T). At T it equals the
  smoothed one. The gate's history, for its chart and for persistence,
  uses α_t for every t, because the smoothed γ_t uses r_{t+1..T}, which
  looks ahead. Viterbi does too.
- **Regime odds ahead.** P(s_{T+h} = k) = (α_T Pʰ)_k for h = 5 and 20
  days (``regime_forecast``).
- **Volatility forecast.** A GARCH(1,1) with persistence φ = a + b and
  long-run variance σ̄² = ω / (1 − φ). Since
  E_T[σ²_{T+j}] = σ̄² + φ^{j−1}(σ²_{T+1} − σ̄²), the h-day average is

      σ̂²_h = σ̄² + (σ²_{T+1} − σ̄²)(1 − φʰ) / (h(1 − φ))

  annualized as √(252 σ̂²_h) (``average_variance_forecast``).
- **A closed gate changes the checklist, never a broker.** Its effects
  (D12):
  - *block adds*: the review says no new risk today
  - *suggest hedge*: it names the hedge sleeve
  - *note*: recorded only

Universe and screens (section 8)
--------------------------------
These read W6's outputs:
- the margin of safety 1 − P / V
- the terminal share PV(TV) / EV
- implied growth against c × the trailing revenue CAGR

The reverse DCF can return several roots. When it does, the check reports
*ambiguous* (a note) rather than choosing one; when the root is unique, it
uses that root. Missing filing fields are counted from W5's loud-failure
list.

Tests, written with the code
----------------------------
- The breach rate at the boundary, simulated, matches 1 − Φ(k) for k ∈
  {0, 1, 1.645, 2}; the power matches 1 − Φ(k − δ/s).
- The Fisher-scale breach agrees with W2's interval at k = 1.96.
- Run lengths: resets, several reviews in one day, gaps.
- D: the null quantiles reproduce the table at the fixed seed, and one
  huge day gives D = x² / (x² + (T − 1)σ²).
- The gate history is filtered: it equals the smoothed probability at T
  and differs before T on a simulated two-regime path.
- The GARCH average matches a Monte Carlo of the recursion.
- The parser: every template row round-trips, a blank turns its rule off,
  and an unreadable value names its line.

For the owner to decide
-----------------------
1. **Evidence k** for each estimated rule. k = 0 is the estimate alone,
   which breaches half the time on the boundary.
2. **N for section 1, in days**, given that persistence is a delay.
3. **Dominated series:** grade the variance-based measures as measured,
   or with the day removed.
4. **Unchanged from REGIME.md:** sections 4–8's values, and section 7's K
   and horizon.
"""
