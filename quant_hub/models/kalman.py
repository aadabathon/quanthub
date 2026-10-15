r"""Linear Gaussian state-space models: Kalman filter, RTS smoother, MLE.

Milestone K1: the filter, the smoother, two structural models and their
maximum-likelihood fit. The derivation below was reviewed before the code.

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
named constant). The first :math:`m` observed innovations mostly identify
the initial state, not the parameters, so :math:`\mathcal T` excludes them
(every later observed :math:`t`), the standard treatment that makes the
likelihood insensitive to :math:`\kappa`. Centering :math:`a_0` at
:math:`y_1` only keeps the numbers small; with :math:`P_0` this wide it
barely moves the result. (Exact diffuse initialization exists but adds
complexity K1 does not need; noted as a possible refinement.)

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
than hiding it. Precisely: :math:`\Delta y_t = \eta_t + \varepsilon_t -
\varepsilon_{t-1}`, so the model implies

.. math::

    \mathrm{Corr}(\Delta y_t, \Delta y_{t-1})
    = -\frac{\sigma_\varepsilon^2}{\sigma_\eta^2 + 2\sigma_\varepsilon^2} \le 0,

and a sample whose changes are positively autocorrelated is fit best at
:math:`\sigma_\varepsilon^2 = 0`. For a true random walk the sample
autocorrelation is positive about half the time.

The search runs over :math:`\log(\sigma^2 / v)` with
:math:`v = \mathrm{Var}(\Delta y)`, which puts every coordinate on order one
as the optimizer wants, inside the box ``LOG_VARIANCE_BOUNDS`` (outside it
the objective is :math:`+\infty`). When the likelihood keeps improving as a
variance shrinks, it is nearly flat there, so the search drifts down and
stops wherever the gain falls below its tolerance, somewhere near the floor.
Any fitted ratio below ``ZERO_LOG_RATIO`` (:math:`e^{-20}`, about
:math:`2\times10^{-9}`) is therefore named in the fit's ``zero_variances``:
read it as zero. A variance that small is economically nil: at 16% annual
volatility (:math:`v \approx 10^{-4}`), a slope variance of
:math:`2\times10^{-9} v` lets the annualized drift wander about 0.2% in a
year.

Computation
-----------
``kalman_filter`` is the reference: numpy, any :math:`m`, one line per
equation above, and it keeps every intermediate. The fit evaluates the
likelihood hundreds of times, and numpy's per-call overhead on 1×1 and 2×2
arrays dominates that loop (about 25 ms for ten years of daily data). So the
fit uses the same recursion written out in scalar arithmetic for
:math:`m \le 2` (about 1.5 ms). The tests hold the two to the same answer.

Validation plan (tests)
-----------------------
1. Filter and smoother equal brute-force Gaussian conditioning of the joint
   :math:`(x_{1:n}, y_{1:n})` on short series (to ~1e-10).
2. The local-level gain converges to :math:`\bar K` above, and the
   converged filter matches an EWMA with :math:`\alpha = \bar K`.
3. On simulated data with known variances: the ±1.96σ filtered band covers
   the true state ≈95% of the time (binomial tolerance); standardized
   innovations have mean ≈ 0, variance ≈ 1 and negligible autocorrelation.
4. MLE recovers the true variances within sampling error, and its maximum
   beats the likelihood at the truth by less than the 99.9% likelihood-ratio
   bound (so the search found a maximum, not just a nearby point). The
   likelihood is invariant to :math:`\kappa` once the first :math:`m` terms
   are excluded. Positively autocorrelated changes are reported as zero
   noise.
5. NaN observations: the update is skipped and the result equals filtering
   the series with that point removed from conditioning.
6. Invalid inputs (shape mismatches, negative variances, non-finite
   parameters) are rejected.
7. The fit's scalar likelihood equals the reference filter's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import ClassVar

import numpy as np

from quant_hub.models.optimize import nelder_mead


DIFFUSE_SCALE = 1e4
r""":math:`\kappa` in units of :math:`\mathrm{Var}(\Delta y)` (Initialization)."""

MIN_OBSERVATIONS = 20
"""Fewest observed values the fitters accept."""

LOG_VARIANCE_BOUNDS = (-30.0, 5.0)
r"""Search box for each :math:`\log(\sigma^2 / v)` in the fit (Estimation)."""

ZERO_LOG_RATIO = -20.0
r"""Fitted :math:`\log(\sigma^2 / v)` below this is reported as zero (Estimation)."""

_LOG_2PI = math.log(2.0 * math.pi)


@dataclass(frozen=True, eq=False)
class StateSpaceModel:
    """Time-invariant model with scalar observations (module docstring).

    ``transition`` is F (m×m), ``observation`` is H as a length-m vector,
    ``state_covariance`` is Q (m×m, symmetric positive semi-definite) and
    ``observation_variance`` is R (> 0). The arrays are copied on
    construction.
    """

    transition: np.ndarray
    observation: np.ndarray
    state_covariance: np.ndarray
    observation_variance: float

    def __post_init__(self) -> None:
        transition = np.array(self.transition, dtype=float)
        if transition.ndim != 2 or transition.shape[0] != transition.shape[1] or not len(transition):
            raise ValueError("transition must be a non-empty square matrix")
        m = transition.shape[0]
        observation = np.array(self.observation, dtype=float)
        if observation.shape != (m,):
            raise ValueError(f"observation must have shape ({m},), got {observation.shape}")
        if not (np.isfinite(transition).all() and np.isfinite(observation).all()):
            raise ValueError("transition and observation must be finite")
        state_covariance = _symmetric_psd(self.state_covariance, m, "state_covariance")
        observation_variance = float(self.observation_variance)
        if not math.isfinite(observation_variance) or observation_variance <= 0:
            raise ValueError("observation_variance must be positive and finite")
        object.__setattr__(self, "transition", transition)
        object.__setattr__(self, "observation", observation)
        object.__setattr__(self, "state_covariance", state_covariance)
        object.__setattr__(self, "observation_variance", observation_variance)

    @property
    def state_dim(self) -> int:
        """:math:`m`, the length of the state vector."""
        return self.transition.shape[0]


@dataclass(frozen=True)
class LocalLevel:
    r"""Random-walk level observed in noise, :math:`m = 1` (module docstring).

    Variances are per trading day, in squared units of :math:`y`.
    """

    level_variance: float  # sigma_eta^2
    noise_variance: float  # sigma_epsilon^2

    state_names: ClassVar[tuple[str, ...]] = ("level",)

    def __post_init__(self) -> None:
        _check_variances(self)

    @property
    def signal_to_noise(self) -> float:
        r""":math:`q = \sigma_\eta^2 / \sigma_\varepsilon^2`."""
        return self.level_variance / self.noise_variance

    @property
    def steady_state_gain(self) -> float:
        r""":math:`\bar K = (\sqrt{q^2 + 4q} - q)/2`, the optimal EWMA weight.

        Computed as the equal :math:`2q / (\sqrt{q^2 + 4q} + q)`, which avoids
        cancellation when :math:`q` is large.
        """
        q = self.signal_to_noise
        if q == 0:
            return 0.0
        return 2.0 * q / (math.sqrt(q * q + 4.0 * q) + q)

    def state_space(self) -> StateSpaceModel:
        return StateSpaceModel(
            transition=np.ones((1, 1)),
            observation=np.ones(1),
            state_covariance=np.full((1, 1), self.level_variance),
            observation_variance=self.noise_variance,
        )


@dataclass(frozen=True)
class LocalLinearTrend:
    r"""Level plus a random-walk slope, :math:`m = 2` (module docstring).

    Variances are per trading day, in squared units of :math:`y`.
    """

    level_variance: float  # sigma_eta^2
    slope_variance: float  # sigma_zeta^2
    noise_variance: float  # sigma_epsilon^2

    state_names: ClassVar[tuple[str, ...]] = ("level", "slope")

    def __post_init__(self) -> None:
        _check_variances(self)

    def state_space(self) -> StateSpaceModel:
        return StateSpaceModel(
            transition=np.array([[1.0, 1.0], [0.0, 1.0]]),
            observation=np.array([1.0, 0.0]),
            state_covariance=np.diag([self.level_variance, self.slope_variance]),
            observation_variance=self.noise_variance,
        )


StructuralModel = LocalLevel | LocalLinearTrend


@dataclass(frozen=True, eq=False)
class KalmanFilterResult:
    r"""Every intermediate of one forward pass; row ``t`` is time ``t``.

    ``innovations`` are NaN where :math:`y_t` is missing and ``gains`` are
    zero there (no update). ``likelihood_mask`` marks the terms in
    :math:`\mathcal T`, the ones ``log_likelihood`` sums.
    """

    model: StateSpaceModel
    predicted_means: np.ndarray  # a_{t|t-1}, (n, m)
    predicted_covariances: np.ndarray  # P_{t|t-1}, (n, m, m)
    filtered_means: np.ndarray  # a_{t|t}, (n, m)
    filtered_covariances: np.ndarray  # P_{t|t}, (n, m, m)
    innovations: np.ndarray  # e_t, (n,)
    innovation_variances: np.ndarray  # S_t, (n,)
    gains: np.ndarray  # K_t, (n, m)
    likelihood_mask: np.ndarray  # t in T, (n,) bool
    log_likelihood: float

    @property
    def standardized_innovations(self) -> np.ndarray:
        r""":math:`e_t / \sqrt{S_t}`: i.i.d. N(0, 1) when the model is right."""
        return self.innovations / np.sqrt(self.innovation_variances)


@dataclass(frozen=True, eq=False)
class SmootherResult:
    """RTS smoothed means :math:`a_{t|n}` (n, m) and covariances :math:`P_{t|n}` (n, m, m)."""

    means: np.ndarray
    covariances: np.ndarray


@dataclass(frozen=True, eq=False)
class StateSpaceSimulation:
    """One simulated path: states :math:`x_t` (n, m) and observations :math:`y_t` (n,)."""

    model: StateSpaceModel
    states: np.ndarray
    observations: np.ndarray


@dataclass(frozen=True, eq=False)
class StructuralFit:
    """Maximum-likelihood fit of a structural model to one series.

    ``filtered`` is the reference filter at the estimate, run from the same
    diffuse prior the likelihood used. ``zero_variances`` names the variances
    the fit drove below ``ZERO_LOG_RATIO``; read those as zero.
    ``iterations`` totals all starting points.
    """

    parameters: StructuralModel
    log_likelihood: float
    filtered: KalmanFilterResult
    zero_variances: tuple[str, ...]
    converged: bool
    iterations: int


def diffuse_prior(observations: np.ndarray, state_dim: int) -> tuple[np.ndarray, np.ndarray]:
    r"""The approximately diffuse prior :math:`(a_0, P_0)` of Initialization.

    :math:`a_0 = (y_1, 0, \dots)` with :math:`y_1` the first observed value,
    and :math:`P_0 = \kappa I` with :math:`\kappa` = ``DIFFUSE_SCALE`` times
    the variance of the changes between consecutive observed values.
    """
    y = _validate_observations(observations)
    if state_dim < 1:
        raise ValueError(f"state_dim must be >= 1, got {state_dim}")
    observed = y[~np.isnan(y)]
    scale = float(np.var(np.diff(observed))) if len(observed) >= 2 else 0.0
    if scale <= 0:
        raise ValueError("a diffuse prior needs observed changes with positive variance")
    mean = np.zeros(state_dim)
    mean[0] = observed[0]
    return mean, DIFFUSE_SCALE * scale * np.eye(state_dim)


def kalman_filter(
    model: StateSpaceModel,
    observations: np.ndarray,
    initial_mean: np.ndarray | None = None,
    initial_covariance: np.ndarray | None = None,
) -> KalmanFilterResult:
    """Run the forward pass (module docstring); NaN observations are missing.

    The prior :math:`x_0 \\sim N(a_0, P_0)` defaults to ``diffuse_prior``;
    pass both ``initial_mean`` and ``initial_covariance`` to set it.
    """
    y = _validate_observations(observations)
    m = model.state_dim
    a, P = _prior(y, m, initial_mean, initial_covariance)
    F, H = model.transition, model.observation
    Q, R = model.state_covariance, model.observation_variance
    identity = np.eye(m)

    n = len(y)
    predicted_means = np.empty((n, m))
    predicted_covariances = np.empty((n, m, m))
    filtered_means = np.empty((n, m))
    filtered_covariances = np.empty((n, m, m))
    innovations = np.full(n, np.nan)
    innovation_variances = np.empty(n)
    gains = np.zeros((n, m))
    for t in range(n):
        # Predict.
        a = F @ a
        P = F @ P @ F.T + Q
        predicted_means[t], predicted_covariances[t] = a, P
        PHt = P @ H
        S = float(H @ PHt) + R
        innovation_variances[t] = S
        if not np.isnan(y[t]):
            # Innovation, gain, and the Joseph-form update.
            e = y[t] - float(H @ a)
            K = PHt / S
            a = a + K * e
            A = identity - np.outer(K, H)
            P = A @ P @ A.T + R * np.outer(K, K)
            P = 0.5 * (P + P.T)
            innovations[t], gains[t] = e, K
        filtered_means[t], filtered_covariances[t] = a, P

    mask = _likelihood_mask(y, m)
    e, S = innovations[mask], innovation_variances[mask]
    log_likelihood = -0.5 * float(np.sum(_LOG_2PI + np.log(S) + e**2 / S))
    return KalmanFilterResult(
        model=model,
        predicted_means=predicted_means,
        predicted_covariances=predicted_covariances,
        filtered_means=filtered_means,
        filtered_covariances=filtered_covariances,
        innovations=innovations,
        innovation_variances=innovation_variances,
        gains=gains,
        likelihood_mask=mask,
        log_likelihood=log_likelihood,
    )


def rts_smoother(filtered: KalmanFilterResult) -> SmootherResult:
    """Run the Rauch–Tung–Striebel backward pass over a filter result.

    Uses the whole sample, so it is for research, never for a decision on
    day ``t`` (module docstring).
    """
    F = filtered.model.transition
    means = filtered.filtered_means.copy()
    covariances = filtered.filtered_covariances.copy()
    for t in range(len(means) - 2, -1, -1):
        P_filtered = filtered.filtered_covariances[t]
        P_next = filtered.predicted_covariances[t + 1]
        # J_t = P_{t|t} F' P_{t+1|t}^{-1}; both covariances are symmetric,
        # so J_t' = P_{t+1|t}^{-1} (F P_{t|t}) is one linear solve.
        try:
            J = np.linalg.solve(P_next, F @ P_filtered).T
        except np.linalg.LinAlgError as exc:
            raise ValueError("a predicted covariance is singular; use a positive-definite prior") from exc
        means[t] = filtered.filtered_means[t] + J @ (means[t + 1] - filtered.predicted_means[t + 1])
        C = P_filtered + J @ (covariances[t + 1] - P_next) @ J.T
        covariances[t] = 0.5 * (C + C.T)
    return SmootherResult(means, covariances)


def simulate_state_space(
    model: StateSpaceModel,
    n_observations: int,
    initial_state: np.ndarray | None = None,
    seed: int | None = None,
) -> StateSpaceSimulation:
    """Draw one path of the model from a known :math:`x_0` (default zeros)."""
    if n_observations < 1:
        raise ValueError(f"n_observations must be >= 1, got {n_observations}")
    m = model.state_dim
    x = np.zeros(m) if initial_state is None else np.array(initial_state, dtype=float)
    if x.shape != (m,) or not np.isfinite(x).all():
        raise ValueError(f"initial_state must be {m} finite values")

    rng = np.random.default_rng(seed)
    state_shocks = rng.standard_normal((n_observations, m)) @ _covariance_factor(model.state_covariance).T
    noise = math.sqrt(model.observation_variance) * rng.standard_normal(n_observations)
    states = np.empty((n_observations, m))
    for t in range(n_observations):
        x = model.transition @ x + state_shocks[t]
        states[t] = x
    return StateSpaceSimulation(model, states, states @ model.observation + noise)


def fit_local_level(observations: np.ndarray, max_iterations: int = 500) -> StructuralFit:
    """Maximum-likelihood local level (Estimation); NaN marks a missing value.

    ``max_iterations`` applies to each starting simplex.
    """
    return _fit(observations, LocalLevel, max_iterations)


def fit_local_linear_trend(observations: np.ndarray, max_iterations: int = 500) -> StructuralFit:
    """Maximum-likelihood local linear trend (Estimation); NaN marks a missing value.

    ``max_iterations`` applies to each starting simplex.
    """
    return _fit(observations, LocalLinearTrend, max_iterations)


def _fit(
    observations: np.ndarray,
    kind: type[LocalLevel] | type[LocalLinearTrend],
    max_iterations: int,
) -> StructuralFit:
    y = _validate_observations(observations)
    observed = y[~np.isnan(y)]
    if len(observed) < MIN_OBSERVATIONS:
        raise ValueError(f"fitting needs at least {MIN_OBSERVATIONS} observed values")
    if max_iterations < 1:
        raise ValueError(f"max_iterations must be >= 1, got {max_iterations}")
    scale = float(np.var(np.diff(observed)))
    if scale <= 0:
        raise ValueError("fitting needs observed changes with positive variance")

    # One prior for every evaluation, so the likelihoods are comparable.
    prior_mean, prior_covariance = diffuse_prior(y, len(kind.state_names))
    low, high = LOG_VARIANCE_BOUNDS

    def decode(x: np.ndarray) -> StructuralModel:
        return kind(*(scale * np.exp(x)).tolist())

    def objective(x: np.ndarray) -> float:
        if np.any(x < low) or np.any(x > high):
            return math.inf
        value = _fast_log_likelihood(decode(x).state_space(), y, prior_mean, prior_covariance)
        return -value if math.isfinite(value) else math.inf

    best = None
    iterations = 0
    for start in _starting_points(kind):
        result = nelder_mead(objective, start, max_iterations)
        iterations += result.iterations
        if best is None or result.value < best.value:
            best = result
    assert best is not None  # the starting points are fixed and non-empty

    parameters = decode(best.x)
    filtered = kalman_filter(parameters.state_space(), y, prior_mean, prior_covariance)
    names = [field.name for field in fields(kind)]
    zero_variances = tuple(name for name, x in zip(names, best.x) if x < ZERO_LOG_RATIO)
    return StructuralFit(
        parameters=parameters,
        log_likelihood=filtered.log_likelihood,
        filtered=filtered,
        zero_variances=zero_variances,
        converged=best.converged,
        iterations=iterations,
    )


def _starting_points(kind: type[LocalLevel] | type[LocalLinearTrend]) -> list[np.ndarray]:
    r"""Starts in :math:`\log(\sigma^2/v)` at :math:`q` = 0.01, 1 and 100.

    For the local level :math:`\mathrm{Var}(\Delta y) = \sigma_\eta^2 +
    2\sigma_\varepsilon^2`, so :math:`\sigma_\eta^2 = qv/(q+2)` and
    :math:`\sigma_\varepsilon^2 = v/(q+2)` match the data at each :math:`q`.
    The trend adds a slope variance of :math:`10^{-6}v`.
    """
    starts = []
    for q in (0.01, 1.0, 100.0):
        level, noise = q / (q + 2.0), 1.0 / (q + 2.0)
        ratios = (level, noise) if kind is LocalLevel else (level, 1e-6, noise)
        starts.append(np.log(ratios))
    return starts


def _fast_log_likelihood(
    model: StateSpaceModel, y: np.ndarray, prior_mean: np.ndarray, prior_covariance: np.ndarray
) -> float:
    """``kalman_filter(...).log_likelihood`` without the bookkeeping (Computation)."""
    if model.state_dim == 1:
        return _log_likelihood_1(model, y, prior_mean, prior_covariance)
    if model.state_dim == 2:
        return _log_likelihood_2(model, y, prior_mean, prior_covariance)
    return kalman_filter(model, y, prior_mean, prior_covariance).log_likelihood


def _log_likelihood_1(
    model: StateSpaceModel, y: np.ndarray, prior_mean: np.ndarray, prior_covariance: np.ndarray
) -> float:
    """The filter recursion for m = 1 in scalar arithmetic."""
    f = float(model.transition[0, 0])
    h = float(model.observation[0])
    q = float(model.state_covariance[0, 0])
    r = model.observation_variance
    a = float(prior_mean[0])
    p = float(prior_covariance[0, 0])
    total = 0.0
    seen = 0
    for y_t in y.tolist():
        a = f * a
        p = f * p * f + q
        if y_t != y_t:  # NaN: missing, so no update
            continue
        s = h * p * h + r
        e = y_t - h * a
        k = p * h / s
        a += k * e
        c = 1.0 - k * h
        p = c * p * c + r * k * k
        seen += 1
        if seen > 1:
            total += _LOG_2PI + math.log(s) + e * e / s
    return -0.5 * total


def _log_likelihood_2(
    model: StateSpaceModel, y: np.ndarray, prior_mean: np.ndarray, prior_covariance: np.ndarray
) -> float:
    """The filter recursion for m = 2 in scalar arithmetic.

    P is symmetric throughout, so it is carried as (p11, p12, p22).
    """
    (f11, f12), (f21, f22) = model.transition.tolist()
    h1, h2 = model.observation.tolist()
    (q11, q12), (_, q22) = model.state_covariance.tolist()
    r = model.observation_variance
    a1, a2 = prior_mean.tolist()
    (p11, p12), (_, p22) = prior_covariance.tolist()
    total = 0.0
    seen = 0
    for y_t in y.tolist():
        # Predict: a = F a, P = F P F' + Q (B = F P).
        a1, a2 = f11 * a1 + f12 * a2, f21 * a1 + f22 * a2
        b11, b12 = f11 * p11 + f12 * p12, f11 * p12 + f12 * p22
        b21, b22 = f21 * p11 + f22 * p12, f21 * p12 + f22 * p22
        p11 = b11 * f11 + b12 * f12 + q11
        p12 = b11 * f21 + b12 * f22 + q12
        p22 = b21 * f21 + b22 * f22 + q22
        if y_t != y_t:  # NaN: missing, so no update
            continue
        g1, g2 = p11 * h1 + p12 * h2, p12 * h1 + p22 * h2  # P H'
        s = h1 * g1 + h2 * g2 + r
        e = y_t - (h1 * a1 + h2 * a2)
        k1, k2 = g1 / s, g2 / s
        a1, a2 = a1 + k1 * e, a2 + k2 * e
        # Joseph form: A P A' + r K K' with A = I - K H (D = A P).
        c11, c12, c21, c22 = 1.0 - k1 * h1, -k1 * h2, -k2 * h1, 1.0 - k2 * h2
        d11, d12 = c11 * p11 + c12 * p12, c11 * p12 + c12 * p22
        d21, d22 = c21 * p11 + c22 * p12, c21 * p12 + c22 * p22
        p11 = d11 * c11 + d12 * c12 + r * k1 * k1
        p12 = d11 * c21 + d12 * c22 + r * k1 * k2
        p22 = d21 * c21 + d22 * c22 + r * k2 * k2
        seen += 1
        if seen > 2:
            total += _LOG_2PI + math.log(s) + e * e / s
    return -0.5 * total


def _validate_observations(observations: np.ndarray) -> np.ndarray:
    y = np.array(observations, dtype=float)
    if y.ndim != 1 or not len(y):
        raise ValueError("observations must be a non-empty one-dimensional array")
    if np.isinf(y).any():
        raise ValueError("observations must be finite, or NaN where missing")
    if np.isnan(y).all():
        raise ValueError("observations contain no observed values")
    return y


def _prior(
    y: np.ndarray,
    m: int,
    initial_mean: np.ndarray | None,
    initial_covariance: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray]:
    if (initial_mean is None) != (initial_covariance is None):
        raise ValueError("pass both initial_mean and initial_covariance, or neither")
    if initial_mean is None:
        return diffuse_prior(y, m)
    mean = np.array(initial_mean, dtype=float)
    if mean.shape != (m,) or not np.isfinite(mean).all():
        raise ValueError(f"initial_mean must be {m} finite values")
    return mean, _symmetric_psd(initial_covariance, m, "initial_covariance")


def _likelihood_mask(y: np.ndarray, m: int) -> np.ndarray:
    """Observed times after the first ``m`` observed ones (Initialization)."""
    observed = np.flatnonzero(~np.isnan(y))
    mask = np.zeros(len(y), dtype=bool)
    mask[observed[m:]] = True
    return mask


def _symmetric_psd(matrix: np.ndarray, m: int, name: str) -> np.ndarray:
    values = np.array(matrix, dtype=float)
    if values.shape != (m, m):
        raise ValueError(f"{name} must have shape ({m}, {m}), got {values.shape}")
    if not np.isfinite(values).all():
        raise ValueError(f"{name} must be finite")
    tolerance = 1e-12 * float(np.abs(values).max())
    if np.abs(values - values.T).max() > tolerance:
        raise ValueError(f"{name} must be symmetric")
    values = 0.5 * (values + values.T)
    if np.linalg.eigvalsh(values).min() < -tolerance:
        raise ValueError(f"{name} must be positive semi-definite")
    return values


def _covariance_factor(covariance: np.ndarray) -> np.ndarray:
    """L with L L' = covariance; eigh rather than Cholesky so singular Q works."""
    eigenvalues, vectors = np.linalg.eigh(covariance)
    return vectors * np.sqrt(np.clip(eigenvalues, 0.0, None))


def _check_variances(parameters: LocalLevel | LocalLinearTrend) -> None:
    for field in fields(parameters):
        value = getattr(parameters, field.name)
        if not math.isfinite(value):
            raise ValueError(f"{field.name} must be finite")
        if value < 0:
            raise ValueError(f"{field.name} must be non-negative")
    if parameters.noise_variance <= 0:
        raise ValueError("noise_variance must be positive (R > 0)")
