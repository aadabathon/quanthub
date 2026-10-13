r"""Linear Gaussian state-space models: Kalman filter, RTS smoother, MLE.

**Status: derivation for owner review (milestone K1).** The implementation
follows once this is approved; nothing below is code yet.

Model
-----
A time-invariant linear Gaussian state-space model, state dimension
:math:`m`, scalar observations (:math:`p = 1` throughout K1):

.. math::

    x_t &= F\,x_{t-1} + w_t, \qquad w_t \sim N(0, Q)   &&\text{(state)}\\
    y_t &= H\,x_t + v_t,     \qquad v_t \sim N(0, R)   &&\text{(observation)}

with :math:`x_0 \sim N(a_0, P_0)` and :math:`w_t, v_t, x_0` mutually and
serially independent. :math:`F` is :math:`m\times m`, :math:`H` is
:math:`1\times m`, :math:`Q \succeq 0`, :math:`R > 0`.

Units: :math:`y_t` is a log price, :math:`t` counts trading days, so every
standard deviation is *per trading day*; the GUI annualizes with
:math:`\sqrt{252}` (D16's convention).

Filter (forward pass)
---------------------
Write :math:`a_{t|s}, P_{t|s}` for the mean and covariance of :math:`x_t`
given :math:`y_1,\dots,y_s`. For :math:`t = 1,\dots,n`:

.. math::

    \text{predict:}\quad & a_{t|t-1} = F a_{t-1|t-1}, \qquad
                           P_{t|t-1} = F P_{t-1|t-1} F^\top + Q\\
    \text{innovation:}\quad & e_t = y_t - H a_{t|t-1}, \qquad
                              S_t = H P_{t|t-1} H^\top + R\\
    \text{gain:}\quad & K_t = P_{t|t-1} H^\top S_t^{-1}\\
    \text{update:}\quad & a_{t|t} = a_{t|t-1} + K_t e_t\\
                        & P_{t|t} = (I - K_t H) P_{t|t-1} (I - K_t H)^\top + K_t R K_t^\top

*Why this is exact.* Given :math:`y_{1:t-1}`, the pair
:math:`(x_t, y_t)` is jointly Gaussian with mean
:math:`(a_{t|t-1}, H a_{t|t-1})` and covariance
:math:`\begin{pmatrix} P & P H^\top \\ H P & H P H^\top + R \end{pmatrix}`
(:math:`P = P_{t|t-1}`). Conditioning a Gaussian on one block is the
Schur-complement formula, which *is* the update above. So each step is exact
Bayesian conditioning, and the whole filter equals conditioning the joint
Gaussian of :math:`(x_{1:n}, y_{1:n})` all at once. The tests use that
equivalence (brute-force conditioning on short series) as the reference.

*Joseph form.* The last line equals :math:`(I - K_t H)P_{t|t-1}` in exact
arithmetic, but stays symmetric and positive semi-definite under rounding;
the short form can drift negative when :math:`R` is small relative to
:math:`P`.

*Missing observations.* If :math:`y_t` is NaN the update is skipped
(:math:`a_{t|t} = a_{t|t-1}`, :math:`P_{t|t} = P_{t|t-1}`) and the term
leaves the likelihood. That is the exact conditional when nothing is
observed.

Likelihood (prediction-error decomposition)
-------------------------------------------
The innovations are independent :math:`N(0, S_t)` given the parameters, so

.. math::

    \log L(\theta) = -\tfrac12 \sum_{t \in \mathcal T}
        \left[\log 2\pi + \log S_t + e_t^2 / S_t\right].

Standardized innovations :math:`e_t/\sqrt{S_t}` are i.i.d. :math:`N(0,1)`
when the model is right: the diagnostic histogram and whiteness check.

Initialization
--------------
The level (and slope) are non-stationary, so there is no stationary prior.
We use an *approximately diffuse* prior: :math:`a_0 = (y_1, 0, \dots)`,
:math:`P_0 = \kappa I` with :math:`\kappa` large relative to the data scale
(:math:`10^4 \times` the sample variance of :math:`\Delta y`, stated as a
named constant). The first :math:`m` innovations mostly identify the
initial state, not the parameters, so :math:`\mathcal T` excludes them
(:math:`t > m`), the standard treatment that makes the likelihood
insensitive to :math:`\kappa`. (Exact diffuse initialization exists but
adds complexity K1 does not need; noted as a possible refinement.)

Smoother (Rauch–Tung–Striebel, backward pass)
---------------------------------------------
With :math:`a_{n|n}, P_{n|n}` from the filter, for :math:`t = n-1,\dots,1`:

.. math::

    J_t &= P_{t|t} F^\top P_{t+1|t}^{-1}\\
    a_{t|n} &= a_{t|t} + J_t\,(a_{t+1|n} - a_{t+1|t})\\
    P_{t|n} &= P_{t|t} + J_t\,(P_{t+1|n} - P_{t+1|t})\,J_t^\top

The smoothed state uses the whole sample: right for *research*
(what was the level?), never for anything trading on day :math:`t`, which
may only use :math:`a_{t|t}`. The page labels them that way.

Models in K1
------------
**Local level** (:math:`m = 1`): :math:`\mu_t = \mu_{t-1} + \eta_t`,
:math:`y_t = \mu_t + \varepsilon_t`, i.e. :math:`F = H = 1`,
:math:`Q = \sigma_\eta^2`, :math:`R = \sigma_\varepsilon^2`.

*Steady state, in closed form.* With signal-to-noise
:math:`q = \sigma_\eta^2/\sigma_\varepsilon^2` and the predicted variance
in units of :math:`R`, :math:`p = P_{t|t-1}/R`, one filter step maps
:math:`p \mapsto p/(1+p) + q`. Its fixed point solves
:math:`p^2 - q\,p - q = 0`, so

.. math::

    \bar p = \tfrac12\left(q + \sqrt{q^2 + 4q}\right), \qquad
    \bar K = \frac{\bar p}{1 + \bar p} = \tfrac12\left(\sqrt{q^2 + 4q} - q\right).

(Check: :math:`\bar p/(1+\bar p) = (q+s)/(q+s+2)` with
:math:`s = \sqrt{q^2+4q}`, and :math:`(s-q)(q+s+2)/2 = (s^2 - q^2 + 2s - 2q)/2
= q + s`.) Once converged the filter is

.. math:: a_{t|t} = a_{t-1|t-1} + \bar K\,(y_t - a_{t-1|t-1}),

an exponentially weighted moving average with :math:`\alpha = \bar K`
(Muth, 1960): **the EWMA is the optimal filter for a random walk observed
in noise, and the noise ratio fixes its weight.** Limits:
:math:`q \to 0 \Rightarrow \bar K \to 0` (a constant level: average
everything); :math:`q \to \infty \Rightarrow \bar K \to 1` (no noise: the
observation is the level).

**Local linear trend** (:math:`m = 2`, :math:`x_t = (\mu_t, \beta_t)`):

.. math::

    \mu_t = \mu_{t-1} + \beta_{t-1} + \eta_t,\quad
    \beta_t = \beta_{t-1} + \zeta_t,\quad
    y_t = \mu_t + \varepsilon_t,

i.e. :math:`F = \begin{pmatrix}1 & 1\\ 0 & 1\end{pmatrix}`,
:math:`H = (1\;\, 0)`, :math:`Q = \mathrm{diag}(\sigma_\eta^2, \sigma_\zeta^2)`,
:math:`R = \sigma_\varepsilon^2`. The slope :math:`\beta_t` is a
time-varying drift (per day in log terms; ×252 annualizes it).

Estimation
----------
Maximum likelihood over log-variances,
:math:`\theta = (\log\sigma_\eta^2, [\log\sigma_\zeta^2], \log\sigma_\varepsilon^2)`,
with the shared Nelder–Mead in ``models/optimize.py`` (the log keeps the
variances positive; multi-start as in GARCH). Expect a boundary on real
daily log prices: :math:`\hat\sigma_\varepsilon^2 \to 0` is common because
daily closes are close to a pure random walk. The page reports that rather
than hiding it.

Validation plan (tests)
-----------------------
1. Filter and smoother equal brute-force Gaussian conditioning of the joint
   :math:`(x_{1:n}, y_{1:n})` on short series (to ~1e-10).
2. The local-level gain converges to :math:`\bar K` above, and the
   converged filter matches an EWMA with :math:`\alpha = \bar K`.
3. On simulated data with known variances: the ±1.96σ filtered band covers
   the true state ≈95% of the time (binomial tolerance); standardized
   innovations have mean ≈ 0, variance ≈ 1 and negligible autocorrelation.
4. MLE recovers the true variances within sampling error; the likelihood
   is invariant to :math:`\kappa` once the first :math:`m` terms are
   excluded.
5. NaN observations: the update is skipped and the result equals filtering
   the series with that point removed from conditioning.
6. Invalid inputs (shape mismatches, negative variances, non-finite
   parameters) are rejected.
"""

from __future__ import annotations
