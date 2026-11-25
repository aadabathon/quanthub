r"""The desk's first voices beyond the baseline (D2, D99).

Each one makes **the same claims as the baseline** (``models/baseline.py``):
the book's return over 5, 21 and 63 sessions as quantiles, the chance of a
10% fall within 21 sessions and of 20% within 63, and its realized
volatility over 21 sessions. Each one differs from the baseline in **one
assumption**, so when the claims are scored (D4) the record says whether
that assumption earns its keep:

=============  ==============================================  ============
voice          what it changes                                 the paper
=============  ==============================================  ============
``garch``      volatility clusters: σ̂ from GARCH(1,1)           Bollerslev
                                                               (1986)
``regimes``    the market has states: a hidden Markov model,   Hamilton
               with each state's own drift and volatility      (1989)
``bootstrap``  the tails are what they've been: resampled      Politis and
               history instead of a normal law                 Romano (1994)
=============  ==============================================  ============

All three work on the book replayed with today's weights
(``baseline.book_log_returns``), from at least ``MIN_SESSIONS`` sessions of
it.

GARCH
-----
The book's daily log returns :math:`r_t` follow GARCH(1,1),
:math:`h_{t+1} = \omega + \alpha (r_t - \mu)^2 + \beta h_t`, fitted by
Gaussian QMLE (``models/garch.py``). The average variance it expects over
the next h sessions is

.. math::  \bar v_h = V_L + (h_{n+1} - V_L)\,\frac{1 - p^h}{h\,(1 - p)},
           \qquad V_L = \frac{\omega}{1 - p},\ p = \alpha + \beta

Its claims are the baseline's with :math:`\hat\sigma^2 h` replaced by
:math:`\bar v_h h`: still no drift, and still a normal law for the
h-session sum. Realized volatility takes :math:`\bar v_{21}\chi^2_{21}/21`,
which ignores GARCH's own volatility of volatility, so its band is too
narrow when volatility is moving. That's an assumption the score can catch.

**Its proposal:** volatility management (Moreira and Muir, 2017). It
scales every holding by :math:`m = \min\bigl(1, \sqrt{V_L / \bar v_{21}}\bigr)`,
less risk when the next month is expected to be rougher than usual and
never more than the book holds. When m is 1 it proposes holding.

Regimes
-------
A Gaussian hidden Markov model (``models/markov.py``), with K ∈ {1, 2, 3}
chosen by BIC, is fitted to the book's daily log returns. From today's
filtered regime probabilities :math:`\alpha_n`, the next h sessions are
**simulated**: each path draws the next regime from the transition matrix
and a return from that regime's normal law. The h-session sum of a Markov
switching process has no closed form (it's a mixture over :math:`K^h`
paths), so ``PATHS`` paths from a fixed seed stand in. With 20,000 paths
the 5% quantile's sampling error is about a tenth of a percent of the
book, and the same inputs give the same claims.

This is the only voice here that claims a **drift**: each regime's mean.
Over two years those means are noisy, and the score will say whether they
help.

**Its proposal:** the same volatility management, at its own forecast:
:math:`m = \min(1, \hat\sigma_{\text{long}} / \hat\sigma_{21})`, with
:math:`\hat\sigma_{21}` the simulated paths' daily volatility and
:math:`\hat\sigma_{\text{long}}` the sample's.

Bootstrap
---------
The stationary bootstrap (Politis and Romano, 1994) resamples the book's
own history in blocks of random length, geometric with mean ``BLOCK``
sessions. That keeps fat tails, and some of the clustering, without any law
chosen for them. Returns are demeaned first: like the baseline, it claims
no drift. Paths and seed as for the regimes. **It proposes holding:** it
has no view on what to change, only on how wide the outcomes are.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from quant_hub.data.desk import LEVELS, Book, Claim, Proposal, VoiceOutput
from quant_hub.models.baseline import (
    FALLS,
    HORIZONS,
    TRADING_DAYS,
    book_log_returns,
    chi2_quantile,
)
from quant_hub.models.garch import average_variance_forecast, fit_garch11, next_variance
from quant_hub.models.markov import hmm_filter, select_regimes

MIN_SESSIONS = 250              # about a year of the book's history
PATHS = 20_000
SEED = 20261125
BLOCK = 10                      # mean block length, sessions
VOL_HORIZON = 21
_Z = [NormalDist().inv_cdf(tau) for tau in LEVELS]


def _history(book: Book, log_returns: pd.DataFrame) -> np.ndarray:
    r = book_log_returns(book, log_returns).dropna().to_numpy()
    if len(r) < MIN_SESSIONS:
        raise ValueError(f"{len(r)} sessions of the book's returns, {MIN_SESSIONS} needed")
    return r


def _normal_claims(variance_per_day: dict[int, float]) -> list[Claim]:
    """The baseline's claims for a zero-drift normal law, with each
    horizon's own average daily variance."""
    claims = [Claim("book.return", h,
                    tuple(math.expm1(z * math.sqrt(variance_per_day[h] * h)) for z in _Z))
              for h in HORIZONS]
    claims += [Claim("book.return", h, event=f"<= {k:g}", probability=NormalDist().cdf(
        math.log1p(k) / math.sqrt(variance_per_day[h] * h))) for h, k in FALLS]
    v = variance_per_day[VOL_HORIZON]
    claims.append(Claim("book.vol", VOL_HORIZON, tuple(
        math.sqrt(TRADING_DAYS * v * chi2_quantile(VOL_HORIZON, tau) / VOL_HORIZON)
        for tau in LEVELS)))
    return claims


def _path_claims(paths: dict[int, np.ndarray]) -> list[Claim]:
    """The same claims from simulated daily log-return paths, one array of
    shape (PATHS, h) per horizon."""
    claims = []
    for h in HORIZONS:
        simple = np.expm1(paths[h].sum(axis=1))
        claims.append(Claim("book.return", h, tuple(float(np.quantile(simple, tau))
                                                    for tau in LEVELS)))
    for h, k in FALLS:
        claims.append(Claim("book.return", h, event=f"<= {k:g}",
                            probability=float(np.mean(np.expm1(paths[h].sum(axis=1)) <= k))))
    vol = np.sqrt(TRADING_DAYS * np.mean(paths[VOL_HORIZON] ** 2, axis=1))
    claims.append(Claim("book.vol", VOL_HORIZON, tuple(float(np.quantile(vol, tau))
                                                       for tau in LEVELS)))
    return claims


def _scaled(book: Book, m: float, why: str) -> Proposal:
    if m >= 1.0 - 1e-9:
        return Proposal(hold=True, note="hold: the next month isn't expected rougher than usual")
    weights = {s: w * m for s, w in book.weights().items()}
    return Proposal(hold=False, weights=weights, note=why)


def garch(book: Book, log_returns: pd.DataFrame) -> VoiceOutput:
    """GARCH(1,1)'s claims and volatility-managed proposal (module docstring)."""
    r = _history(book, log_returns)
    fit = fit_garch11(r)
    p = fit.parameters
    h_next = next_variance(p, float(r[-1]), float(fit.conditional_variances[-1]))
    v = {h: float(average_variance_forecast(p, h_next, np.array([h]))[0])
         for h in (*HORIZONS, VOL_HORIZON)}
    m = min(1.0, math.sqrt(p.unconditional_variance / v[VOL_HORIZON]))
    annual = lambda var: math.sqrt(TRADING_DAYS * var)  # noqa: E731
    why = (f"Volatility clusters. GARCH(1,1) on the book: tomorrow "
           f"{annual(h_next):.1%} a year, the next 21 sessions {annual(v[21]):.1%}, the "
           f"long run {annual(p.unconditional_variance):.1%} (persistence {p.persistence:.3f}).")
    proposal = _scaled(book, m, f"scale risk to {m:.0%}: the next month is expected "
                                f"rougher than the long run")
    return VoiceOutput("garch", "1", tuple(_normal_claims(v)), proposal, why,
                       {"alpha": round(p.alpha, 6), "beta": round(p.beta, 6),
                        "omega": p.omega, "converged": fit.converged, "sessions": len(r)})


def regimes(book: Book, log_returns: pd.DataFrame) -> VoiceOutput:
    """A hidden Markov model's simulated claims (module docstring)."""
    r = _history(book, log_returns)
    fit = select_regimes(r, (1, 2, 3)).chosen
    model = fit.model
    today = hmm_filter(model, r).filtered[-1]
    rng = np.random.default_rng(SEED)
    longest = max(*HORIZONS, VOL_HORIZON)
    k = model.n_regimes
    cumulative = np.cumsum(model.transition, axis=1)
    state = np.searchsorted(np.cumsum(today @ model.transition), rng.random(PATHS))
    state = np.minimum(state, k - 1)
    daily = np.empty((PATHS, longest))
    for d in range(longest):
        if d:
            u = rng.random(PATHS)
            state = np.minimum((u[:, None] > cumulative[state]).sum(axis=1), k - 1)
        daily[:, d] = model.means[state] + model.sigmas[state] * rng.standard_normal(PATHS)
    paths = {h: daily[:, :h] for h in (*HORIZONS, VOL_HORIZON)}
    sigma_21 = float(daily[:, :VOL_HORIZON].std())
    sigma_long = float(r.std(ddof=1))
    m = min(1.0, sigma_long / sigma_21)
    states = ", ".join(f"{p:.0%} in the {s * math.sqrt(TRADING_DAYS):.0%}-a-year state"
                       for p, s in zip(today, model.sigmas))
    why = (f"The market has states. {k} by BIC; today {states}. Each state keeps its own "
           f"drift, so this is the one voice here with a view on direction.")
    proposal = _scaled(book, m, f"scale risk to {m:.0%}: the regimes expect a rougher "
                                f"month than the book's history")
    return VoiceOutput("regimes", "1", tuple(_path_claims(paths)), proposal, why,
                       {"regimes": k, "today": [round(float(p), 4) for p in today],
                        "means": [float(x) for x in model.means],
                        "sigmas": [float(x) for x in model.sigmas],
                        "paths": PATHS, "seed": SEED, "sessions": len(r)})


def bootstrap(book: Book, log_returns: pd.DataFrame) -> VoiceOutput:
    """The stationary bootstrap's claims (module docstring)."""
    r = _history(book, log_returns)
    demeaned = r - r.mean()
    n = len(demeaned)
    rng = np.random.default_rng(SEED)
    longest = max(*HORIZONS, VOL_HORIZON)
    index = rng.integers(0, n, PATHS)
    daily = np.empty((PATHS, longest))
    for d in range(longest):
        if d:
            jump = rng.random(PATHS) < 1.0 / BLOCK
            index = np.where(jump, rng.integers(0, n, PATHS), (index + 1) % n)
        daily[:, d] = demeaned[index]
    paths = {h: daily[:, :h] for h in (*HORIZONS, VOL_HORIZON)}
    why = (f"The tails are what they've been: {n} sessions of the book resampled in blocks "
           f"of about {BLOCK}, no drift, no normal law.")
    return VoiceOutput("bootstrap", "1", tuple(_path_claims(paths)), Proposal(hold=True), why,
                       {"block": BLOCK, "paths": PATHS, "seed": SEED, "sessions": n})
