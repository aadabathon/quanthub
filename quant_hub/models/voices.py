r"""The desk's first voices beyond the baseline (D2, D99; D2b).

Each one makes **the same claims as the baseline** (``models/baseline.py``):
the return over 5, 21, 63 and 252 sessions as quantiles, the chance of
ending 10% down after 21 sessions and 20% after 63, the realized
volatility over 21 sessions, and (since D2b) the deepest fall within 21,
63 and 252 sessions, as quantiles and as the chances of falling 10% and
20%. It makes them about **the book** and, when the snapshot priced it,
about **the market** (SPY), each from that target's own history. Each one
differs from the baseline in **one assumption**, so when the claims are
scored (D4) the record says whether that assumption earns its keep:

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

Each target needs at least ``MIN_SESSIONS`` sessions of history. The book
always does, or the voice says nothing; the market is left out when it
hasn't.

GARCH
-----
A target's daily log returns :math:`r_t` follow GARCH(1,1),
:math:`h_{t+1} = \omega + \alpha (r_t - \mu)^2 + \beta h_t`, fitted by
Gaussian QMLE (``models/garch.py``). The average variance it expects over
the next h sessions is

.. math::  \bar v_h = V_L + (h_{n+1} - V_L)\,\frac{1 - p^h}{h\,(1 - p)},
           \qquad V_L = \frac{\omega}{1 - p},\ p = \alpha + \beta

Its return and volatility claims are the baseline's with
:math:`\hat\sigma^2 h` replaced by :math:`\bar v_h h`: still no drift, and
still a normal law for the h-session sum. Realized volatility takes
:math:`\bar v_{21}\chi^2_{21}/21`, which ignores GARCH's own volatility of
volatility, so its band is too narrow when volatility is moving. That's an
assumption the score can catch.

**Its deepest fall** is path-dependent, so it's simulated from the
recursion itself: from today's :math:`h_{n+1}`, each day draws
:math:`\varepsilon = \sqrt{h}\,z` with no drift and updates
:math:`h \leftarrow \omega + \alpha\varepsilon^2 + \beta h`. ``PATHS``
paths from a fixed seed; the fall is read off them as in the baseline
(``baseline.deepest_falls``).

**Its proposal:** volatility management (Moreira and Muir, 2017), from the
book's fit. It scales every holding by
:math:`m = \min\bigl(1, \sqrt{V_L / \bar v_{21}}\bigr)`, less risk when the
next month is expected to be rougher than usual and never more than the
book holds. When m is 1 it proposes holding.

Regimes
-------
A Gaussian hidden Markov model (``models/markov.py``), with K ∈ {1, 2, 3}
chosen by BIC, is fitted to a target's daily log returns. From today's
filtered regime probabilities :math:`\alpha_n`, the next 252 sessions are
**simulated**: each path draws the next regime from the transition matrix
and a return from that regime's normal law. The h-session sum of a Markov
switching process has no closed form (it's a mixture over :math:`K^h`
paths), so ``PATHS`` paths from a fixed seed stand in. With 20,000 paths
the 5% quantile's sampling error is about a tenth of a percent of the
book, and the same inputs give the same claims. Every claim, the fall
included, is read off the same paths.

This is the only voice here that claims a **drift**: each regime's mean.
Over two years those means are noisy, and the score will say whether they
help.

**Its proposal:** the same volatility management, from the book's paths:
:math:`m = \min(1, \hat\sigma_{\text{long}} / \hat\sigma_{21})`, with
:math:`\hat\sigma_{21}` the simulated paths' daily volatility over 21
sessions and :math:`\hat\sigma_{\text{long}}` the sample's.

Bootstrap
---------
The stationary bootstrap (Politis and Romano, 1994) resamples history in
blocks of random length, geometric with mean ``BLOCK`` sessions. That keeps
fat tails, and some of the clustering, without any law chosen for them.
Returns are demeaned first: like the baseline, it claims no drift. The book
and the market are resampled **together**, the same sessions for both, on
the sessions both have. Paths and seed as for the regimes. **It proposes
holding:** it has no view on what to change, only on how wide the outcomes
are.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from statistics import NormalDist

import numpy as np
import pandas as pd

from quant_hub.data.desk import LEVELS, Book, Claim, Proposal, VoiceOutput
from quant_hub.models.baseline import (
    FALL_HORIZONS,
    FALLS,
    HORIZONS,
    TRADING_DAYS,
    VOL_HORIZON,
    book_log_returns,
    chi2_quantile,
    deepest_falls,
    fall_claims,
    market_log_returns,
)
from quant_hub.models.garch import (
    GARCH11Parameters,
    average_variance_forecast,
    fit_garch11,
    next_variance,
)
from quant_hub.models.markov import hmm_filter, select_regimes

VERSION = "2"
MIN_SESSIONS = 250              # about a year of a target's history
# Each voice's canon papers (data/canon): its one assumption, and the
# volatility management two of them propose.
PAPERS = {
    "garch": ("bollerslev1986", "moreiramuir2017"),
    "regimes": ("hamilton1989", "moreiramuir2017"),
    "bootstrap": ("politisromano1994",),
}
PATHS = 20_000
SEED = 20261125
BLOCK = 10                      # mean block length, sessions
LONGEST = max(*HORIZONS, VOL_HORIZON, *FALL_HORIZONS)
_Z = [NormalDist().inv_cdf(tau) for tau in LEVELS]


def _history(series: pd.Series, name: str) -> np.ndarray:
    r = series.dropna().to_numpy()
    if len(r) < MIN_SESSIONS:
        raise ValueError(f"{len(r)} sessions of {name}'s returns, {MIN_SESSIONS} needed")
    return r


def _targets(book: Book, log_returns: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    """The book (its history is required) and the market, when the snapshot
    priced it with enough history."""
    targets = [("book", _history(book_log_returns(book, log_returns), "the book"))]
    market = market_log_returns(book, log_returns)
    if market is not None and market.dropna().size >= MIN_SESSIONS:
        targets.append((book.market, market.dropna().to_numpy()))
    return targets


def _normal_claims(target: str, variance_per_day: dict[int, float]) -> list[Claim]:
    """The baseline's return and volatility claims for a zero-drift normal
    law, with each horizon's own average daily variance."""
    claims = [Claim(f"{target}.return", h,
                    tuple(math.expm1(z * math.sqrt(variance_per_day[h] * h)) for z in _Z))
              for h in HORIZONS]
    claims += [Claim(f"{target}.return", h, event=f"<= {k:g}", probability=NormalDist().cdf(
        math.log1p(k) / math.sqrt(variance_per_day[h] * h))) for h, k in FALLS]
    v = variance_per_day[VOL_HORIZON]
    claims.append(Claim(f"{target}.vol", VOL_HORIZON, tuple(
        math.sqrt(TRADING_DAYS * v * chi2_quantile(VOL_HORIZON, tau) / VOL_HORIZON)
        for tau in LEVELS)))
    return claims


def path_claims(target: str, daily: np.ndarray) -> list[Claim]:
    """Every claim from simulated daily log returns, shape (PATHS, LONGEST)."""
    claims = []
    for h in HORIZONS:
        simple = np.expm1(daily[:, :h].sum(axis=1))
        claims.append(Claim(f"{target}.return", h, tuple(float(np.quantile(simple, tau))
                                                          for tau in LEVELS)))
    for h, k in FALLS:
        claims.append(Claim(f"{target}.return", h, event=f"<= {k:g}", probability=float(
            np.mean(np.expm1(daily[:, :h].sum(axis=1)) <= k))))
    vol = np.sqrt(TRADING_DAYS * np.mean(daily[:, :VOL_HORIZON] ** 2, axis=1))
    claims.append(Claim(f"{target}.vol", VOL_HORIZON, tuple(float(np.quantile(vol, tau))
                                                            for tau in LEVELS)))
    return claims + fall_claims(target, deepest_falls(daily.T, FALL_HORIZONS))


def _scaled(book: Book, m: float, why: str) -> Proposal:
    if m >= 1.0 - 1e-9:
        return Proposal(hold=True, note="hold: the next month isn't expected rougher than usual")
    weights = {s: w * m for s, w in book.weights().items()}
    return Proposal(hold=False, weights=weights, note=why)


def garch_steps(p: GARCH11Parameters, h_next: float, rng: np.random.Generator,
                paths: int = PATHS, days: int = LONGEST) -> Iterator[np.ndarray]:
    """Driftless GARCH(1,1) days from today's variance ``h_next``, one
    session at a time (module docstring)."""
    h = np.full(paths, h_next)
    for _ in range(days):
        eps = np.sqrt(h) * rng.standard_normal(paths)
        yield eps
        h = p.omega + p.alpha * eps ** 2 + p.beta * h


def garch(book: Book, log_returns: pd.DataFrame) -> VoiceOutput:
    """GARCH(1,1)'s claims and volatility-managed proposal (module docstring)."""
    claims, fits = [], {}
    for target, r in _targets(book, log_returns):
        fit = fit_garch11(r)
        p = fit.parameters
        h_next = next_variance(p, float(r[-1]), float(fit.conditional_variances[-1]))
        v = {h: float(average_variance_forecast(p, h_next, np.array([h]))[0])
             for h in (*HORIZONS, VOL_HORIZON)}
        claims += _normal_claims(target, v)
        falls = deepest_falls(garch_steps(p, h_next, np.random.default_rng(SEED)),
                              FALL_HORIZONS)
        claims += fall_claims(target, falls)
        fits[target] = (fit, p, h_next, v, len(r))
    fit, p, h_next, v, n = fits["book"]
    m = min(1.0, math.sqrt(p.unconditional_variance / v[VOL_HORIZON]))
    annual = lambda var: math.sqrt(TRADING_DAYS * var)  # noqa: E731
    why = (f"Volatility clusters. GARCH(1,1) on the book: tomorrow "
           f"{annual(h_next):.1%} a year, the next 21 sessions {annual(v[21]):.1%}, the "
           f"long run {annual(p.unconditional_variance):.1%} (persistence {p.persistence:.3f}).")
    proposal = _scaled(book, m, f"scale risk to {m:.0%}: the next month is expected "
                                f"rougher than the long run")
    parameters = {"alpha": round(p.alpha, 6), "beta": round(p.beta, 6), "omega": p.omega,
                  "converged": fit.converged, "sessions": n, "paths": PATHS, "seed": SEED}
    if book.market in fits:
        mp = fits[book.market][1]
        parameters.update({"market_alpha": round(mp.alpha, 6), "market_beta": round(mp.beta, 6),
                           "market_omega": mp.omega})
    return VoiceOutput("garch", VERSION, tuple(claims), proposal, why, parameters)


def _regime_paths(r: np.ndarray):
    """A BIC-chosen HMM fitted to ``r`` and ``PATHS`` simulated paths of
    ``LONGEST`` days from today's filtered state (module docstring)."""
    model = select_regimes(r, (1, 2, 3)).chosen.model
    today = hmm_filter(model, r).filtered[-1]
    rng = np.random.default_rng(SEED)
    k = model.n_regimes
    cumulative = np.cumsum(model.transition, axis=1)
    state = np.searchsorted(np.cumsum(today @ model.transition), rng.random(PATHS))
    state = np.minimum(state, k - 1)
    daily = np.empty((PATHS, LONGEST))
    for d in range(LONGEST):
        if d:
            u = rng.random(PATHS)
            state = np.minimum((u[:, None] > cumulative[state]).sum(axis=1), k - 1)
        daily[:, d] = model.means[state] + model.sigmas[state] * rng.standard_normal(PATHS)
    return model, today, daily


def regimes(book: Book, log_returns: pd.DataFrame) -> VoiceOutput:
    """A hidden Markov model's simulated claims (module docstring)."""
    claims, fits = [], {}
    for target, r in _targets(book, log_returns):
        model, today, daily = _regime_paths(r)
        claims += path_claims(target, daily)
        fits[target] = (model, today, float(daily[:, :VOL_HORIZON].std()), r)
    model, today, sigma_21, r = fits["book"]
    m = min(1.0, float(r.std(ddof=1)) / sigma_21)
    k = model.n_regimes
    states = ", ".join(f"{p:.0%} in the {s * math.sqrt(TRADING_DAYS):.0%}-a-year state"
                       for p, s in zip(today, model.sigmas))
    why = (f"The market has states. {k} by BIC; today {states}. Each state keeps its own "
           f"drift, so this is the one voice here with a view on direction.")
    proposal = _scaled(book, m, f"scale risk to {m:.0%}: the regimes expect a rougher "
                                f"month than the book's history")
    parameters = {"regimes": k, "today": [round(float(p), 4) for p in today],
                  "means": [float(x) for x in model.means],
                  "sigmas": [float(x) for x in model.sigmas],
                  "paths": PATHS, "seed": SEED, "sessions": len(r)}
    if book.market in fits:
        market_model, market_today = fits[book.market][:2]
        parameters.update({"market_regimes": market_model.n_regimes,
                           "market_today": [round(float(p), 4) for p in market_today]})
    return VoiceOutput("regimes", VERSION, tuple(claims), proposal, why, parameters)


def bootstrap(book: Book, log_returns: pd.DataFrame) -> VoiceOutput:
    """The stationary bootstrap's claims (module docstring)."""
    frame = book_log_returns(book, log_returns).to_frame("book")
    market = market_log_returns(book, log_returns)
    if market is not None:
        joint = pd.concat([frame["book"], market], axis=1).dropna()
        frame = joint if len(joint) >= MIN_SESSIONS else frame.dropna()
    frame = frame.dropna()
    _history(frame["book"], "the book")
    demeaned = (frame - frame.mean()).to_numpy()
    n = len(demeaned)
    rng = np.random.default_rng(SEED)
    index = rng.integers(0, n, PATHS)
    daily = np.empty((len(frame.columns), PATHS, LONGEST))
    for d in range(LONGEST):
        if d:
            jump = rng.random(PATHS) < 1.0 / BLOCK
            index = np.where(jump, rng.integers(0, n, PATHS), (index + 1) % n)
        daily[:, :, d] = demeaned[index].T
    claims = []
    for column, target in enumerate(frame.columns):
        claims += path_claims(target, daily[column])
    why = (f"The tails are what they've been: {n} sessions resampled in blocks of about "
           f"{BLOCK}, no drift, no normal law.")
    return VoiceOutput("bootstrap", VERSION, tuple(claims), Proposal(hold=True), why,
                       {"block": BLOCK, "paths": PATHS, "seed": SEED, "sessions": n})
