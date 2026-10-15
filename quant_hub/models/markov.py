r"""Gaussian hidden Markov model of daily returns: filter, smoother, Viterbi, EM.

Milestone M1. The derivation below was reviewed before the code.

Model
-----
A hidden regime :math:`s_t \in \{1,\dots,K\}` follows a time-homogeneous
first-order Markov chain,

.. math::

    \Pr(s_t = j \mid s_{t-1} = i,\ \text{all earlier history}) = p_{ij},
    \qquad \textstyle\sum_j p_{ij} = 1,

and :math:`\Pr(s_1 = i) = \pi_i`. Given the whole regime path, the returns
are independent with

.. math::

    r_t \mid s_t = i \;\sim\; N(\mu_i, \sigma_i^2), \qquad
    b_i(r) = \frac{1}{\sqrt{2\pi}\,\sigma_i}
             \exp\!\left(-\frac{(r-\mu_i)^2}{2\sigma_i^2}\right).

So each day's return is a normal mixture whose weights are the regime
probabilities, and regime persistence makes calm and turbulent days
cluster.

Data and units: :math:`r_t = \ln(C_t / C_{t-1})` from a ``PriceHistory``'s
closes (any source, as in the GARCH and Kalman Labs). Everything is per
trading day; the page annualizes :math:`\mu \times 252` and
:math:`\sigma \times \sqrt{252}` (D16), and durations are in trading days.

Filter (forward pass)
---------------------
Write :math:`\alpha_t(j) = \Pr(s_t = j \mid r_{1:t})` (filtered) and
:math:`\bar\alpha_t(j) = \Pr(s_t = j \mid r_{1:t-1})` (predicted). For
:math:`t = 1,\dots,n`:

.. math::

    \text{predict:}\quad & \bar\alpha_t(j) = \textstyle\sum_i \alpha_{t-1}(i)\,p_{ij},
                           \qquad \bar\alpha_1 = \pi\\
    \text{evidence:}\quad & c_t = \textstyle\sum_j \bar\alpha_t(j)\,b_j(r_t)
                            = f(r_t \mid r_{1:t-1})\\
    \text{update:}\quad & \alpha_t(j) = \bar\alpha_t(j)\,b_j(r_t) / c_t

*Why this is exact.* The predict line is the law of total probability
over yesterday's regime (the chain is Markov). The update is Bayes' rule:
given :math:`s_t`, today's return carries no further dependence on the
past, so the likelihood of :math:`s_t = j` is :math:`b_j(r_t)` and
:math:`c_t` is the normalizer. Then, as in the Kalman filter's
prediction-error decomposition,

.. math::  \log L = \log f(r_{1:n}) = \textstyle\sum_{t=1}^n \log c_t .

*Scaling.* The unnormalized forward variable :math:`f(r_{1:t}, s_t = j)`
is a product of :math:`t` densities. A daily return density is around 25
(σ ≈ 1%), so it passes the largest double (:math:`10^{308}`) within about
220 days. Normalizing every step (above) keeps every number a
probability, and the scale goes into :math:`\log c_t`.

*Extreme days.* One more guard: on a −20% day with every
:math:`\sigma_j \le 0.5\%`, :math:`z = 40` and each :math:`b_j(r_t)
\approx e^{-800}`, which is exactly zero in double precision. Then
:math:`c_t = 0` and :math:`\log L = -\infty`. So emissions are computed
in logs, :math:`\ell_t(j) = \log b_j(r_t)`, and shifted by their largest
value :math:`m_t = \max_j \ell_t(j)`:

.. math::

    \tilde b_t(j) = e^{\ell_t(j) - m_t} \in (0, 1], \qquad
    \log c_t = m_t + \log \textstyle\sum_j \bar\alpha_t(j)\,\tilde b_t(j).

The shift cancels in :math:`\alpha_t`, and the largest term is exactly 1.

One corner remains. If the best-fitting regime is (nearly) impossible today
(:math:`\bar\alpha_t(j) \approx 0` there) and every possible one is
more than ~640 nats worse, the shifted sum still underflows. That day is
redone fully in logs, shifting by :math:`\max_j[\log\bar\alpha_t(j) +
\ell_t(j)]`. The answer is exact for the probabilities a double can hold.
A probability below :math:`10^{-308}` is zero, and the paths through it
are lost; only a log-space filter keeps them, at many times the cost. The
point is that :math:`\log L` never becomes :math:`-\infty` or NaN, which
would wreck a fit. It can happen in practice: EM drives transitions that
are never observed toward zero.

Smoother (backward pass)
------------------------
Write :math:`\gamma_t(i) = \Pr(s_t = i \mid r_{1:n})` (smoothed) and
:math:`\xi_t(i,j) = \Pr(s_t = i, s_{t+1} = j \mid r_{1:n})`.

*Key fact.* Once :math:`s_{t+1}` is known, later returns say nothing more
about :math:`s_t`:

.. math::

    \Pr(s_t, s_{t+1}, r_{1:n})
      = f(r_{1:t}, s_t)\; p_{s_t s_{t+1}}\; f(r_{t+1:n} \mid s_{t+1}),

and the last factor doesn't involve :math:`s_t`, so it cancels when
normalizing over :math:`s_t`:

.. math::

    \Pr(s_t = i \mid s_{t+1} = j, r_{1:n})
      = \Pr(s_t = i \mid s_{t+1} = j, r_{1:t})
      = \frac{\alpha_t(i)\,p_{ij}}{\bar\alpha_{t+1}(j)} .

Multiplying by :math:`\gamma_{t+1}(j)` and summing over :math:`j` gives,
for :math:`t = n-1,\dots,1` from :math:`\gamma_n = \alpha_n`:

.. math::

    \xi_t(i,j) &= \alpha_t(i)\,p_{ij}\,\frac{\gamma_{t+1}(j)}{\bar\alpha_{t+1}(j)}\\
    \gamma_t(i) &= \textstyle\sum_j \xi_t(i,j)
                 = \alpha_t(i) \sum_j p_{ij}\,\frac{\gamma_{t+1}(j)}{\bar\alpha_{t+1}(j)}

(Kim, 1994). Like the RTS smoother, it uses only the filter's output and
the transition matrix, never the returns again. It equals Baum's
:math:`\beta` recursion (:math:`\gamma = \alpha\hat\beta`) but needs no
second scaling. Where :math:`\bar\alpha_{t+1}(j) = 0`, regime :math:`j`
is impossible that day, so :math:`\gamma_{t+1}(j) = 0` too and the ratio
is taken as 0.

Viterbi (most likely regime path)
---------------------------------
:math:`\delta_t(j)` is the log joint density of the best path ending in
regime :math:`j` on day :math:`t`:

.. math::

    \delta_1(j) &= \log\pi_j + \ell_1(j)\\
    \delta_t(j) &= \max_i\,[\delta_{t-1}(i) + \log p_{ij}] + \ell_t(j),
                   \qquad \psi_t(j) = \arg\max_i\,[\cdots]

then :math:`\hat s_n = \arg\max_j \delta_n(j)` and
:math:`\hat s_t = \psi_{t+1}(\hat s_{t+1})`. It's dynamic programming: the
path score is a sum, so the best path into :math:`(t, j)` extends a best
path into some :math:`(t-1, i)`. In logs nothing overflows, and
:math:`\log 0 = -\infty` handles forbidden moves. Ties go to the lower
index.

The Viterbi path maximizes the probability of the *whole* path. Day by
day it can differ from :math:`\arg\max_i \gamma_t(i)`, and the day-by-day
choice can even string together an impossible path.

What may be traded on
---------------------
* **Filtered** :math:`\alpha_t` uses returns through close :math:`t`.
  Tomorrow's regime odds are :math:`\bar\alpha_{t+1} = \alpha_t P`, known
  at close :math:`t`. A position decided then earns :math:`t \to t+1`
  (D33's one-bar lag).
* **Smoothed** :math:`\gamma_t` and the **Viterbi** path use returns
  after :math:`t`. They're hindsight, for research only, and the page
  labels them that way (as the Kalman Lab does).
* **Parameters** are fitted on the whole window, so even in-sample
  :math:`\alpha_t` carries parameter look-ahead. Truly out-of-sample
  regimes need parameters fitted on data up to :math:`t` (walk-forward),
  which is the backtester's job (R). The filter is cheap; refits are not.

What the model implies
----------------------
* **Long-run occupancy:** the stationary distribution :math:`\bar\pi`
  solves :math:`\bar\pi P = \bar\pi`, :math:`\sum_i \bar\pi_i = 1`. It's
  the long-run share of days in each regime, unique when every regime can
  reach every other. Computed by least squares on the stacked system
  :math:`[P^\top - I;\ \mathbf 1^\top]\,\bar\pi = [0;\ 1]`, which is exact
  when the solution is unique.
* **Durations:** once in regime :math:`i`, each day it stays with
  probability :math:`p_{ii}`, so a stay lasts :math:`D` days with
  :math:`\Pr(D = d) = p_{ii}^{d-1}(1 - p_{ii})`, and

  .. math::  E[D] = \frac{1}{1 - p_{ii}} \ \text{trading days}.

* **Volatility clustering:** let :math:`v_i = E[r^2 \mid s = i] =
  \sigma_i^2 + \mu_i^2`. Given the regimes, different days' returns are
  independent, so for :math:`h \ge 1`,
  :math:`\mathrm{Cov}(r_t^2, r_{t+h}^2) = \mathrm{Cov}(v_{s_t}, v_{s_{t+h}})`.
  For :math:`K = 2`, :math:`P^h = \mathbf 1\bar\pi + \lambda^h(I - \mathbf
  1\bar\pi)` with :math:`\lambda = p_{11} + p_{22} - 1`, so

  .. math::  \mathrm{Cov}(r_t^2, r_{t+h}^2) = \mathrm{Var}(v_{s})\,\lambda^h

  in the stationary chain. Squared returns' autocovariance decays
  geometrically, as in GARCH with :math:`\lambda` in place of
  :math:`\alpha + \beta`. For larger :math:`K` it's a mix of the powers of
  :math:`P`'s other eigenvalues.
* **Forecasts:** :math:`q_h = \alpha_n P^h` gives
  :math:`\Pr(s_{n+h} = j \mid r_{1:n})`, tending to :math:`\bar\pi`. The
  predictive variance of day :math:`n+h` is
  :math:`\sum_j q_{h,j}(\sigma_j^2 + \mu_j^2) - (\sum_j q_{h,j}\mu_j)^2`:
  a regime-based expected-vol term structure to set beside GARCH's (D30).

Estimation: EM (Baum–Welch)
---------------------------
With the regimes known, the log-likelihood would be

.. math::

    \log f(r, s) = \log\pi_{s_1} + \sum_{t=2}^n \log p_{s_{t-1}s_t}
                   + \sum_{t=1}^n \log b_{s_t}(r_t).

**E-step.** Its expectation over the regimes, given the returns and the
current parameters :math:`\theta'`, is

.. math::

    Q(\theta \mid \theta') = \text{const}
      + \sum_{t=1}^{n-1}\sum_{i,j} \xi_t(i,j)\log p_{ij}
      + \sum_{t=1}^n \sum_i \gamma_t(i)\log b_i(r_t),

with :math:`\gamma, \xi` from the smoother under :math:`\theta'`. The
constant holds :math:`\pi`, which is fixed (below).

**M-step** (closed form):

* Each row of :math:`P` maximizes :math:`\sum_j N_{ij}\log p_{ij}`
  subject to :math:`\sum_j p_{ij} = 1`, where
  :math:`N_{ij} = \sum_{t=1}^{n-1}\xi_t(i,j)`. A Lagrange multiplier
  gives :math:`p_{ij} = N_{ij} / \sum_k N_{ik}`: the expected number of
  :math:`i \to j` moves over the expected number of moves out of
  :math:`i`. Computed without an :math:`n \times K \times K` array:
  :math:`N_{ij} = p_{ij}\sum_t \alpha_t(i)\,\gamma_{t+1}(j)/\bar\alpha_{t+1}(j)`,
  one matrix product.
* Regime :math:`i`'s term is a normal log-likelihood with day weights
  :math:`\gamma_t(i)`, so with :math:`w_i = \sum_t \gamma_t(i)`:

  .. math::

      \mu_i = \frac{1}{w_i}\sum_t \gamma_t(i)\,r_t, \qquad
      \sigma_i^2 = \frac{1}{w_i}\sum_t \gamma_t(i)\,(r_t - \mu_i)^2 .

**The likelihood never decreases.** For any :math:`\theta`,
:math:`\log L(\theta) = Q(\theta \mid \theta') - H(\theta \mid \theta')`
with :math:`H(\theta \mid \theta') = E_{\theta'}[\log p_\theta(s \mid r)]`.
Gibbs' inequality gives :math:`H(\theta \mid \theta') \le
H(\theta' \mid \theta')`, so

.. math::

    \log L(\theta) - \log L(\theta')
      \ge Q(\theta \mid \theta') - Q(\theta' \mid \theta') \ge 0

when :math:`\theta` maximizes :math:`Q`. The tests check it at every
iteration.

**The initial distribution is fixed at uniform,** :math:`\pi_i = 1/K`,
not estimated:

* Only day 1 informs it: its EM update is :math:`\gamma_1`, which lands
  on a corner.
* It enters :math:`\log L` through day 1 alone. Against the stationary
  :math:`\bar\pi`, the likelihood ratio lies between
  :math:`1/(K\max_i\bar\pi_i)` and :math:`1/(K\min_i\bar\pi_i)`, so the
  two log-likelihoods differ by at most :math:`\max_i|\log(K\bar\pi_i)|`,
  however long the series.
* Fixed, it keeps every M-step closed form, so the monotonicity above
  holds exactly. Setting :math:`\pi = \bar\pi(P)` would not.

The simulator starts from :math:`\bar\pi`, so simulated series are
stationary from day 1.

**A variance floor** (``VARIANCE_FLOOR_RATIO``). The likelihood is
unbounded without one. Set :math:`\mu_i = r_t` for one day and let
:math:`\sigma_i \to 0`: :math:`b_i(r_t) \to \infty` while the other
regimes still explain the other days, so :math:`L \to \infty`.

* EM drifts into such one-day "regimes" from bad starts, or on series with
  many identical returns (stale prices, zero-return days).
* So :math:`\sigma_i^2 \ge v_{\min} = 0.01 \times` the sample variance,
  i.e. :math:`\sigma_i \ge 10\%` of the sample's daily σ. For SPY's
  stored history (1.07% a day) that's 0.11% a day, 1.7% annualized, far
  below any real regime.
* The constrained M-step stays exact. For fixed :math:`\mu_i`, the
  :math:`Q` term in :math:`\sigma_i^2` is
  :math:`-\tfrac{w_i}{2}(\log\sigma^2 + S_i/\sigma^2)`, with :math:`S_i`
  the weighted variance above. That's unimodal with its peak at
  :math:`S_i`, so on :math:`[v_{\min}, \infty)` the maximizer is
  :math:`\max(S_i, v_{\min})`. :math:`\mu_i`'s update doesn't involve
  :math:`\sigma_i`. EM stays monotone for the constrained likelihood.
* A fit that ends with some :math:`\sigma_i` on the floor says so
  (``at_floor``), like the Kalman fit's zero variances.

**Starting points and short runs.** EM climbs to a local maximum that
depends on where it starts. The starts:

1. *Volatility quantiles:* 20-day trailing realized vol
   (``ROLLING_WINDOW``, about a month) sorts the days into :math:`K`
   equal-count groups. Each group's mean and variance start
   :math:`\mu_i, \sigma_i^2`. :math:`P` starts with
   :math:`p_{ii} = 1 - 1/20` (an expected stay as long as the window) and
   equal off-diagonals.
2. ``N_RANDOM_STARTS`` = 4 random starts, seeded by ``FIT_SEED`` so a fit
   is reproducible: :math:`\sigma_i = \hat\sigma e^{u_i}` with
   :math:`u_i \sim U(-1, 1)`, :math:`\mu_i = \hat\mu`, and
   :math:`p_{ii} \sim U(0.8, 0.99)`.

Each start runs ``SHORT_RUN`` = 25 iterations. The best by
:math:`\log L` continues until an iteration gains less than
``EM_TOLERANCE`` (:math:`10^{-6}` nats). These "short runs of EM"
(Biernacki, Celeux & Govaert, 2003) cost about one full run.

Otherwise it stops at ``MAX_ITERATIONS`` = 500 and reports that it didn't
converge. Measured: identified fits converge in 10–120 iterations. A fit
that runs out usually has more regimes than the data supports, and EM
crawls along a flat ridge. On i.i.d. returns, a 3-regime fit still
gained 2e-5 nats an iteration after 2,000 iterations (25 s), with perhaps
0.2 nats left to gain. So "didn't converge" is itself a sign that
:math:`K` is too big; compare BIC.

Measured, three regimes on 2,520 simulated days: from the
volatility-quantile start, EM converged in 96 iterations, and iteration
20 was already within 0.06 nats of the maximum.

**Labels:** after the fit, states are sorted by :math:`\sigma` ascending,
permuting :math:`\mu`, :math:`\sigma` and :math:`P`'s rows and columns
together. Regime 0 is always the calmest. Otherwise labels are arbitrary
(label switching), and TRUE | FIT couldn't line up.

Choosing K: BIC
---------------
.. math::

    \mathrm{BIC}_K = -2\log L_K + d_K \log n, \qquad d_K = K(K+1),

counting :math:`K` means, :math:`K` variances and :math:`K(K-1)` free
transition probabilities (:math:`\pi` is fixed). The candidates are
:math:`K = 1, 2, 3`, where :math:`K = 1` is "no regimes" (i.i.d. normal,
closed form). The smallest BIC wins.

Caveat: comparing :math:`K` with :math:`K + 1` is a non-regular problem.
The extra regime's parameters are unidentified under the smaller model, so
the usual likelihood-ratio asymptotics fail, and there's no p-value here.
BIC is the standard practical criterion. The tests check that it picks
:math:`K = 1` on i.i.d. normal returns and the true :math:`K` on clearly
separated synthetic regimes.

Computation
-----------
One numpy implementation. The filter and smoother loop over days; the
emissions, the M-step and :math:`N` are vectorized. Measured on 2,520
days with :math:`K = 3`:

* A forward–backward pass takes about 11 ms.
* A fit is roughly 5 × 25 + 75 ≈ 200 passes, so 2–3 s. At worst it's
  5 × 25 + 475 = 600 passes, about 7 s.
* The BIC sweep over :math:`K = 1, 2, 3` takes about 5 s on ten years. It
  runs on the worker thread.

Also measured, and rejected:

* Pure-Python loops: no faster.
* Computing the forward pass as :math:`\log_2 n` rounds of batched matrix
  products (matrix products are associative): it matched to
  :math:`10^{-14}` and was 2.2× faster. That's not worth maintaining a
  second implementation.

Limitations
-----------
* Returns are normal *within* a regime, so within-regime fat tails remain.
  Student-t emissions are a possible refinement; their M-step needs an
  inner iteration.
* Durations are geometric (memoryless). Hidden semi-Markov models relax
  that.
* :math:`P` is constant: switching odds don't depend on anything
  observable.
* Parameter look-ahead in-sample (above).
* Closes are unadjusted until F lands. A split shows as one enormous
  return and would get a regime of its own. Dividends are a small
  negative return on ex-dates (SPY: about 0.3%, four times a year).
* No standard errors in M1: EM doesn't produce them (Louis's method or a
  numerical Hessian would). TRUE | FIT on synthetic data shows the
  estimation error instead.

Validation plan (tests)
-----------------------
1. **Brute force.** The filter (:math:`\alpha, \bar\alpha, \log L`), the
   smoother (:math:`\gamma, \xi`) and Viterbi equal enumeration of all
   :math:`K^n` regime paths on short series (:math:`n = 7`,
   :math:`K = 2, 3`), to about 1e-12.
2. **Monotone EM.** :math:`\log L` never decreases, every iteration,
   including when the floor binds.
3. **Calibration.** On long simulations with the true parameters, the
   filtered and smoothed probabilities are calibrated: in each probability
   decile, the share of days truly in the regime matches the mean
   probability within binomial tolerance.
4. **Recovery.** On simulated 2- and 3-regime series, fitted
   :math:`\mu, \sigma, p_{ii}` fall within a few standard errors of the
   truth. The fitted :math:`\log L` is never below its value at the true
   parameters, so the search found the maximum and not just a nearby
   point (the C3 lesson).
5. **BIC** picks :math:`K = 1` on i.i.d. normal returns and the true
   :math:`K` on separated 2- and 3-regime series.
6. **Closed forms.** Simulated occupancy matches :math:`\bar\pi`. Mean
   stay lengths match :math:`1/(1 - p_{ii})`. For :math:`K = 2`, the
   sample autocovariance of :math:`r^2` matches
   :math:`\mathrm{Var}(v_s)\lambda^h`.
7. **The floor.** On a series with 30% exact-zero returns, EM without a
   floor collapses a regime onto the zeros (:math:`\sigma \approx
   10^{-150}`, :math:`\log L > 10^5`). With it, the fit converges with
   finite :math:`\log L`, every :math:`\sigma \ge` the floor, and
   ``at_floor`` reported.
8. **Labels** come out sorted by :math:`\sigma`. Permuting a start's
   labels gives the same fit.
9. **Extreme days.** A −20% day among 1% days leaves :math:`\log L`
   finite and equal to a log-space forward pass. A day whose best regime
   is impossible is redone in logs (Filter: extreme days).
10. **Invalid inputs are rejected:** a non-stochastic :math:`P`,
    :math:`\sigma \le 0`, non-finite returns, fewer than
    ``MIN_OBSERVATIONS`` days, :math:`K < 1`.
11. **Too many regimes** on i.i.d. returns shows as "didn't converge"
    (Estimation).

API (mirrors ``models/kalman.py``)
----------------------------------
* ``GaussianHMM(means, sigmas, transition)``: validated; has
  ``stationary_distribution``, ``expected_durations`` and ``ordered()``.
* ``hmm_filter(model, returns) -> HMMFilterResult`` holds filtered,
  predicted and ``log_likelihood``.
* ``hmm_smoother(filtered) -> HMMSmootherResult`` holds smoothed and the
  transition counts :math:`N` (it takes the filter's result, like
  ``rts_smoother``).
* ``viterbi(model, returns)``, ``simulate_hmm(model, n, seed)`` and
  ``regime_forecast(model, filtered_last, horizon)``.
* ``fit_hmm(returns, n_regimes) -> HMMFit`` holds the model, log L, BIC,
  iterations, converged and ``at_floor``.
* ``select_regimes(returns, candidates=(1, 2, 3))`` returns every fit and
  the BIC choice.
"""


from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


VARIANCE_FLOOR_RATIO = 0.01
r""":math:`v_{\min}` as a fraction of the sample variance of returns (Estimation)."""

ROLLING_WINDOW = 20
"""Trading days of trailing realized vol for the quantile start (Estimation)."""

N_RANDOM_STARTS = 4
"""Random EM starts besides the volatility-quantile one (Estimation)."""

SHORT_RUN = 25
"""EM iterations each start gets before the best one continues (Estimation)."""

EM_TOLERANCE = 1e-6
"""Converged when an EM iteration gains less than this many nats of log L."""

MAX_ITERATIONS = 500
"""EM iterations before a fit stops and reports it didn't converge (Estimation)."""

FIT_SEED = 20261015
"""Seed for the random starts, so a fit is reproducible."""

MIN_OBSERVATIONS = 100
"""Fewest returns a fit accepts: K = 3 has 12 parameters, and each regime
must be entered and left several times for its p_ii to mean anything."""

ROW_SUM_TOLERANCE = 1e-9
"""How far a transition row's sum may sit from 1 (rounding, not modelling)."""

MIN_EVIDENCE = 1e-280
r"""A shifted evidence :math:`\sum_j \bar\alpha_t(j)\tilde b_t(j)` below this
is near double precision's subnormal range (Filter: extreme days), so that
day is recomputed fully in logs."""

_LOG_2PI = math.log(2.0 * math.pi)


@dataclass(frozen=True, eq=False)
class GaussianHMM:
    """K regimes: ``means`` and ``sigmas`` (K,) per trading day, and the
    row-stochastic ``transition`` matrix P (K×K), with
    ``transition[i, j]`` = Pr(s_t = j | s_{t-1} = i). The initial
    distribution is uniform (module docstring). Arrays are copied.
    """

    means: np.ndarray
    sigmas: np.ndarray
    transition: np.ndarray

    def __post_init__(self) -> None:
        means = np.array(self.means, dtype=float)
        if means.ndim != 1 or not len(means):
            raise ValueError("means must be a non-empty one-dimensional array")
        k = len(means)
        sigmas = np.array(self.sigmas, dtype=float)
        transition = np.array(self.transition, dtype=float)
        if sigmas.shape != (k,) or transition.shape != (k, k):
            raise ValueError(f"{k} regimes need sigmas of shape ({k},) and a {k}×{k} "
                             "transition matrix")
        if not (np.isfinite(means).all() and np.isfinite(sigmas).all()
                and np.isfinite(transition).all()):
            raise ValueError("means, sigmas and transition must be finite")
        if (sigmas <= 0).any():
            raise ValueError("sigmas must be positive")
        off_by = np.abs(transition.sum(axis=1) - 1)
        if (transition < 0).any() or (off_by > ROW_SUM_TOLERANCE).any():
            raise ValueError("each transition row must be probabilities summing to 1")
        for name, value in (("means", means), ("sigmas", sigmas), ("transition", transition)):
            value.setflags(write=False)
            object.__setattr__(self, name, value)

    @property
    def n_regimes(self) -> int:
        return len(self.means)

    @property
    def initial(self) -> np.ndarray:
        r"""The fixed uniform :math:`\pi` (module docstring, Estimation)."""
        return np.full(self.n_regimes, 1.0 / self.n_regimes)

    @property
    def stationary_distribution(self) -> np.ndarray:
        r""":math:`\bar\pi`: :math:`\bar\pi P = \bar\pi`, summing to 1 (least squares)."""
        k = self.n_regimes
        system = np.vstack([self.transition.T - np.eye(k), np.ones(k)])
        target = np.zeros(k + 1)
        target[-1] = 1.0
        pi = np.clip(np.linalg.lstsq(system, target, rcond=None)[0], 0.0, None)
        return pi / pi.sum()

    @property
    def expected_durations(self) -> np.ndarray:
        r""":math:`1/(1 - p_{ii})` trading days; infinite for an absorbing regime."""
        stay = np.diag(self.transition)
        with np.errstate(divide="ignore"):
            return np.where(stay < 1.0, 1.0 / (1.0 - stay), math.inf)

    def permuted(self, order: Sequence[int]) -> GaussianHMM:
        """The same model with regime ``order[k]`` relabelled ``k``."""
        order = np.asarray(order)
        if sorted(order.tolist()) != list(range(self.n_regimes)):
            raise ValueError("order must be a permutation of the regime labels")
        return GaussianHMM(self.means[order], self.sigmas[order],
                           self.transition[np.ix_(order, order)])

    def ordered(self) -> GaussianHMM:
        """Regimes sorted by sigma, calmest first (module docstring, Labels)."""
        return self.permuted(np.argsort(self.sigmas, kind="stable"))


@dataclass(frozen=True, eq=False)
class HMMFilterResult:
    r"""One forward pass; row ``t`` is day ``t``.

    ``filtered`` is :math:`\alpha_t`, ``predicted`` is :math:`\bar\alpha_t`
    (row 0 is :math:`\pi`), ``log_evidence`` is :math:`\log c_t` and
    ``log_likelihood`` their sum.
    """

    model: GaussianHMM
    filtered: np.ndarray  # (n, K)
    predicted: np.ndarray  # (n, K)
    log_evidence: np.ndarray  # (n,)
    log_likelihood: float


@dataclass(frozen=True, eq=False)
class HMMSmootherResult:
    r"""Smoothed :math:`\gamma_t` (n, K), and the expected transition counts
    :math:`N_{ij} = \sum_t \xi_t(i, j)` (K, K)."""

    smoothed: np.ndarray
    transition_counts: np.ndarray


@dataclass(frozen=True, eq=False)
class HMMSimulation:
    """One simulated path: regimes (n,) as labels 0..K-1, and returns (n,)."""

    model: GaussianHMM
    states: np.ndarray
    returns: np.ndarray


@dataclass(frozen=True, eq=False)
class RegimeForecast:
    r"""Row ``h - 1`` is day :math:`n + h`: regime probabilities
    :math:`q_h = \alpha_n P^h` (H, K), and the predictive mean and variance
    of that day's return (H,)."""

    probabilities: np.ndarray
    means: np.ndarray
    variances: np.ndarray


@dataclass(frozen=True, eq=False)
class HMMFit:
    """Maximum-likelihood (EM) fit of a K-regime model to one series.

    ``model`` has its regimes sorted by sigma. ``at_floor`` names the
    regimes whose variance ended on ``variance_floor``.
    ``log_likelihood_path`` is the winning start's log L before its first
    iteration and after every one (short run, then continuation), so
    ``iterations`` is its length - 1.
    """

    model: GaussianHMM
    log_likelihood: float
    n_observations: int
    iterations: int
    converged: bool
    at_floor: tuple[int, ...]
    variance_floor: float
    log_likelihood_path: np.ndarray

    @property
    def n_parameters(self) -> int:
        """:math:`d_K = K(K+1)`: means, variances, free transition probabilities."""
        k = self.model.n_regimes
        return k * (k + 1)

    @property
    def bic(self) -> float:
        return -2.0 * self.log_likelihood + self.n_parameters * math.log(self.n_observations)


@dataclass(frozen=True, eq=False)
class RegimeSelection:
    """Fits for each candidate K; ``chosen`` has the smallest BIC."""

    fits: tuple[HMMFit, ...]

    @property
    def chosen(self) -> HMMFit:
        return min(self.fits, key=lambda fit: fit.bic)


# --------------------------------------------------------------------------
# Filter, smoother, Viterbi
# --------------------------------------------------------------------------

def hmm_filter(model: GaussianHMM, returns: np.ndarray) -> HMMFilterResult:
    """The forward pass (module docstring, Filter)."""
    r = _validate_returns(returns)
    log_b = _log_emissions(model, r)
    shift = log_b.max(axis=1)
    b = np.exp(log_b - shift[:, None])
    n, k = b.shape
    transition = model.transition
    filtered = np.empty((n, k))
    predicted = np.empty((n, k))
    log_evidence = np.empty(n)
    a = model.initial
    for t in range(n):
        predicted[t] = a
        u = a * b[t]
        c = u.sum()
        if c > MIN_EVIDENCE:
            log_evidence[t] = shift[t] + math.log(c)
        else:   # the best-fitting regime is (nearly) impossible today: redo in logs
            with np.errstate(divide="ignore"):
                x = np.log(a) + log_b[t]
            top = x.max()
            u = np.exp(x - top)
            c = u.sum()
            log_evidence[t] = top + math.log(c)
        filtered[t] = u / c
        a = filtered[t] @ transition
    return HMMFilterResult(model, filtered, predicted, log_evidence, float(log_evidence.sum()))


def hmm_smoother(filtered: HMMFilterResult) -> HMMSmootherResult:
    """Kim's backward pass and the expected transition counts (module docstring)."""
    alpha, predicted = filtered.filtered, filtered.predicted
    transition = filtered.model.transition
    # Where a regime was impossible (predicted 0) its smoothed probability is
    # 0 too, so dividing by 1 there makes the ratio the required 0.
    safe = np.where(predicted > 0, predicted, 1.0)
    smoothed = np.empty_like(alpha)
    smoothed[-1] = g = alpha[-1]
    for t in range(len(alpha) - 2, -1, -1):
        g = alpha[t] * (transition @ (g / safe[t + 1]))
        smoothed[t] = g
    counts = transition * (alpha[:-1].T @ (smoothed[1:] / safe[1:]))
    return HMMSmootherResult(smoothed, counts)


def viterbi(model: GaussianHMM, returns: np.ndarray) -> np.ndarray:
    """The most likely regime path, labels 0..K-1 (module docstring, Viterbi)."""
    r = _validate_returns(returns)
    log_b = _log_emissions(model, r)
    n, k = log_b.shape
    with np.errstate(divide="ignore"):
        log_p = np.log(model.transition)
        delta = np.log(model.initial) + log_b[0]
    back = np.zeros((n, k), dtype=np.intp)
    columns = np.arange(k)
    for t in range(1, n):
        scores = delta[:, None] + log_p          # [i, j]: best path into i, then i -> j
        back[t] = scores.argmax(axis=0)          # first maximum: ties go to the lower index
        delta = scores[back[t], columns] + log_b[t]
    path = np.empty(n, dtype=np.intp)
    path[-1] = int(delta.argmax())
    for t in range(n - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path


# --------------------------------------------------------------------------
# Simulation and forecasts
# --------------------------------------------------------------------------

def simulate_hmm(model: GaussianHMM, n_observations: int,
                 seed: int | None = None) -> HMMSimulation:
    """Day 1's regime from the stationary distribution, then the chain;
    returns N(mu_s, sigma_s^2). A fixed ``seed`` makes it deterministic."""
    if n_observations < 1:
        raise ValueError("n_observations must be at least 1")
    rng = np.random.default_rng(seed)
    k = model.n_regimes
    first = int(rng.choice(k, p=model.stationary_distribution))
    draws = rng.random(n_observations).tolist()
    cumulative = [np.cumsum(row).tolist() for row in model.transition]
    states = [first]
    for u in draws[1:]:
        row = cumulative[states[-1]]
        j = 0
        while j < k - 1 and u >= row[j]:
            j += 1
        states.append(j)
    labels = np.array(states, dtype=np.intp)
    returns = model.means[labels] + model.sigmas[labels] * rng.standard_normal(n_observations)
    return HMMSimulation(model, labels, returns)


def regime_forecast(model: GaussianHMM, filtered_last: np.ndarray,
                    horizon: int) -> RegimeForecast:
    """Regime odds and predictive moments for days n+1..n+horizon (module docstring)."""
    q = np.array(filtered_last, dtype=float)
    if q.shape != (model.n_regimes,) or (q < 0).any() or abs(q.sum() - 1) > ROW_SUM_TOLERANCE:
        raise ValueError("filtered_last must be one day's regime probabilities")
    if horizon < 1:
        raise ValueError("horizon must be at least 1 day")
    rows = []
    for _ in range(horizon):
        q = q @ model.transition
        rows.append(q)
    probabilities = np.array(rows)
    means = probabilities @ model.means
    variances = probabilities @ (model.sigmas**2 + model.means**2) - means**2
    return RegimeForecast(probabilities, means, variances)


# --------------------------------------------------------------------------
# Estimation
# --------------------------------------------------------------------------

def fit_hmm(returns: np.ndarray, n_regimes: int, *, seed: int = FIT_SEED,
            max_iterations: int = MAX_ITERATIONS) -> HMMFit:
    """EM from several starts, short runs, then the best to convergence
    (module docstring, Estimation)."""
    r = _validate_returns(returns, minimum=MIN_OBSERVATIONS)
    if n_regimes < 1:
        raise ValueError("n_regimes must be at least 1")
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    if r.max() == r.min():
        raise ValueError("the returns don't vary: there is nothing to fit")
    sample_variance = float(r.var())
    floor = VARIANCE_FLOOR_RATIO * sample_variance

    if n_regimes == 1:              # i.i.d. normal: the closed-form maximum
        model = GaussianHMM([r.mean()], [math.sqrt(sample_variance)], [[1.0]])
        log_l = hmm_filter(model, r).log_likelihood
        return HMMFit(model, log_l, len(r), 0, True, (), floor, np.array([log_l]))

    rng = np.random.default_rng(seed)
    starts = [_quantile_start(r, n_regimes, floor),
              *(_random_start(r, n_regimes, rng) for _ in range(N_RANDOM_STARTS))]
    short = min(SHORT_RUN, max_iterations)
    runs = [_em(r, start, floor, short) for start in starts]
    model, path, converged = max(runs, key=lambda run: run[1][-1])
    if not converged and max_iterations > short:
        model, more, converged = _em(r, model, floor, max_iterations - short)
        path = path + more[1:]
    model = model.ordered()
    at_floor = tuple(int(i) for i in np.flatnonzero(model.sigmas**2 <= floor * (1 + 1e-12)))
    return HMMFit(model, path[-1], len(r), len(path) - 1, converged, at_floor, floor,
                  np.array(path))


def select_regimes(returns: np.ndarray,
                   candidates: Sequence[int] = (1, 2, 3)) -> RegimeSelection:
    """Fit each candidate K; the smallest BIC is ``chosen`` (module docstring)."""
    if not candidates:
        raise ValueError("candidates must name at least one regime count")
    return RegimeSelection(tuple(fit_hmm(returns, k) for k in candidates))


def _em(r: np.ndarray, model: GaussianHMM, floor: float,
        iterations: int) -> tuple[GaussianHMM, list[float], bool]:
    """Up to ``iterations`` EM steps; the path holds log L before and after each."""
    filtered = hmm_filter(model, r)
    path = [filtered.log_likelihood]
    for _ in range(iterations):
        model = _m_step(r, model, hmm_smoother(filtered), floor)
        filtered = hmm_filter(model, r)
        path.append(filtered.log_likelihood)
        if path[-1] - path[-2] < EM_TOLERANCE:
            return model, path, True
    return model, path, False


def _m_step(r: np.ndarray, model: GaussianHMM, smoothed: HMMSmootherResult,
            floor: float) -> GaussianHMM:
    """The closed-form maximizer of Q (module docstring, M-step). A regime
    with zero expected days (or moves) leaves Q unchanged whatever its
    parameters, so it keeps its old ones."""
    gamma, counts = smoothed.smoothed, smoothed.transition_counts
    weights = gamma.sum(axis=0)
    used = weights > 0
    means = model.means.copy()
    means[used] = (gamma.T @ r)[used] / weights[used]
    spread = (gamma * (r[:, None] - means) ** 2).sum(axis=0)
    variances = model.sigmas**2
    variances[used] = np.maximum(spread[used] / weights[used], floor)
    leaving = counts.sum(axis=1)
    moved = leaving > 0
    transition = model.transition.copy()
    transition[moved] = counts[moved] / leaving[moved, None]
    return GaussianHMM(means, np.sqrt(variances), transition)


def _quantile_start(r: np.ndarray, k: int, floor: float) -> GaussianHMM:
    """Days grouped by trailing realized vol (module docstring, start 1)."""
    window = ROLLING_WINDOW
    squares = np.concatenate([[0.0], np.cumsum(r**2)])
    t = np.arange(1, len(r) + 1)
    first = np.maximum(t - window, 0)         # an expanding window for the first days
    realized = (squares[t] - squares[first]) / (t - first)
    groups = np.array_split(np.argsort(realized, kind="stable"), k)
    means = [float(r[g].mean()) for g in groups]
    sigmas = [math.sqrt(max(float(r[g].var()), floor)) for g in groups]
    return GaussianHMM(means, sigmas, _persistent_transition(np.full(k, 1.0 - 1.0 / window)))


def _random_start(r: np.ndarray, k: int, rng: np.random.Generator) -> GaussianHMM:
    """A seeded random start (module docstring, start 2)."""
    sigmas = r.std() * np.exp(rng.uniform(-1.0, 1.0, k))
    return GaussianHMM(np.full(k, r.mean()), sigmas,
                       _persistent_transition(rng.uniform(0.8, 0.99, k)))


def _persistent_transition(stay: np.ndarray) -> np.ndarray:
    """Diagonal ``stay``, the rest of each row spread evenly."""
    k = len(stay)
    transition = np.repeat(((1.0 - stay) / (k - 1))[:, None], k, axis=1)
    np.fill_diagonal(transition, stay)
    return transition


def _log_emissions(model: GaussianHMM, r: np.ndarray) -> np.ndarray:
    r""":math:`\ell_t(j) = \log b_j(r_t)`, (n, K)."""
    z = (r[:, None] - model.means) / model.sigmas
    return -0.5 * z * z - np.log(model.sigmas) - 0.5 * _LOG_2PI


def _validate_returns(returns: np.ndarray, minimum: int = 1) -> np.ndarray:
    r = np.array(returns, dtype=float)
    if r.ndim != 1 or len(r) < minimum:
        noun = "value" if minimum == 1 else "values"
        raise ValueError(f"returns must be a one-dimensional array of at least {minimum} {noun}")
    if not np.isfinite(r).all():
        raise ValueError("returns must be finite")
    return r
