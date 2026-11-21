r"""What-if vectors: weights to compare, their builders and references (ALLOCATION A3).

A **vector** is a set of weights, each a symbol's share of equity
:math:`w_i \ge 0`, with the rest in cash: :math:`c = 1 - \sum_i w_i \ge 0`.
It never becomes a stored positions snapshot (ALLOCATION finding 5).
``to_snapshot`` makes a transient one for ``PortfolioService.analyse``, so a
vector runs through the same analysis Portfolio does. Each holding is
valued at :math:`w_i E` on a notional equity :math:`E`. The analysis is
scale-free, so :math:`E` doesn't matter.

Builders
========

* **A picks total** (``scale_picks``). The picks, every holding outside
  the core, are scaled together to a total :math:`T` with their mix kept:
  :math:`w_i' = T\,w_i / \sum_{j \notin C} w_j`. The core is unchanged and
  the rest is cash.
* **An index and cash** (``index_and_cash``): :math:`w_\text{index} = x`.
* **Picks netted into a core at a target β** (``net_into_core``). The
  picks keep their weights, and the core takes the weight that brings the
  book to :math:`\beta^*`: :math:`w_C = (\beta^* - \sum_{i \notin C} w_i\beta_i) /
  \beta_C`. That's A1's netting (``models/allocation.py``) solved for the
  core. It's refused when :math:`w_C < 0` (the picks alone carry more than
  :math:`\beta^*`) or the total passes 1 (it needs leverage).

References (long-only, on the vector's own names)
=================================================

Each keeps the vector's invested share :math:`s = \sum_i w_i` and spreads it
over the same names. The weights are a softmax, so they're positive and
add to :math:`s` by construction:

.. math::  w_i = s\,\frac{e^{\theta_i}}{\sum_j e^{\theta_j}}, \qquad \theta_1 = 0

and ``models/optimize.nelder_mead`` searches the other :math:`\theta`. With
:math:`\Sigma` the sample covariance of the daily returns over the
lookback, and :math:`\mu` their mean excess over cash:

* **Minimum variance** (Markowitz 1952): minimize :math:`w^\top \Sigma w`.
  For two assets the answer is
  :math:`w_1/s = (\sigma_2^2 - \sigma_{12}) / (\sigma_1^2 + \sigma_2^2 - 2\sigma_{12})`
  when that lies in :math:`[0, 1]`.
* **Equal risk contribution** (Maillard, Roncalli and Teïletche 2010):
  each name's share of the variance,
  :math:`\mathrm{RC}_i = w_i (\Sigma w)_i / w^\top \Sigma w`, is
  :math:`1/n`. The search minimizes :math:`\sum_i (\mathrm{RC}_i - 1/n)^2`.
  For two assets it's inverse vol, :math:`w_i \propto 1/\sigma_i`, whatever
  the correlation.
* **Equal weight** (DeMiguel, Garlappi and Uppal 2009): :math:`w_i = s/n`,
  the naive mix the optimizers often fail to beat out of sample.
* **Maximum Sharpe:** maximize :math:`w^\top \mu / \sqrt{w^\top \Sigma w}`.
  With every :math:`(\Sigma^{-1}\mu)_i > 0`, it's the tangency portfolio
  :math:`w \propto \Sigma^{-1}\mu`. **It's in-sample, and shown only with
  that warning** (decision 6). It loads on whatever did well in the
  lookback, which is Michaud's (1989) "error maximization": the
  estimation error in :math:`\mu` dominates the answer.

What it can't say
-----------------
:math:`\Sigma` and :math:`\mu` are estimates over one lookback. The
references are lessons to look at, not targets to trade, and nothing on
the page optimizes your book.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Mapping
from datetime import datetime

import numpy as np
import pandas as pd

from quant_hub.data.positions import Holding, Position, PositionSnapshot
from quant_hub.models.optimize import nelder_mead

NOTIONAL = 100_000.0
"""The equity a vector is valued on; the analysis is scale-free."""
TRADING_DAYS = 252
_TOLERANCE = 1e-9
_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9.\-]{0,9})\s*[:,=\s]\s*([-+]?\d+(?:\.\d+)?)\s*%?\s*$")


# ------------------------------------------------------------------ vectors

def validate(weights: Mapping[str, float]) -> dict[str, float]:
    """Upper-cased symbols, weights in [0, 1] adding to at most 1 (the rest
    is cash). Zero weights are dropped. Raises ``ValueError`` naming the
    problem."""
    out: dict[str, float] = {}
    for symbol, weight in weights.items():
        name = symbol.strip().upper()
        if not name:
            raise ValueError("a weight needs a symbol")
        if not (math.isfinite(weight) and 0.0 <= weight <= 1.0):
            raise ValueError(f"{name}: a weight is a share of equity between 0 and 100%")
        if name in out:
            raise ValueError(f"{name} appears twice")
        if weight > 0:
            out[name] = float(weight)
    total = sum(out.values())
    if total > 1.0 + _TOLERANCE:
        raise ValueError(f"the weights add to {total:.1%}: more than the equity needs leverage")
    return out


def cash(weights: Mapping[str, float]) -> float:
    return max(0.0, 1.0 - sum(weights.values()))


def parse(text: str) -> dict[str, float]:
    """One holding a line, a symbol and its percent of equity ("SPY 60",
    "QQQ: 12.5%", "IEF,20"). Blank lines and lines starting with # are
    skipped. Raises ``ValueError`` naming the line."""
    weights: dict[str, float] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            raise ValueError(f"line {number}: write a symbol and its percent, e.g. SPY 60")
        symbol = m.group(1).upper()
        if symbol in weights:
            raise ValueError(f"line {number}: {symbol} appears twice")
        weights[symbol] = float(m.group(2)) / 100.0
    return validate(weights)


def to_snapshot(weights: Mapping[str, float], name: str, as_of: datetime,
                equity: float = NOTIONAL) -> PositionSnapshot:
    """A transient snapshot of the vector (module docstring); never stored."""
    weights = validate(weights)
    if not weights:
        raise ValueError("a vector needs at least one holding")
    positions = [Position(s, Holding.EQUITY, w * equity, value=w * equity)
                 for s, w in sorted(weights.items())]
    rest = cash(weights) * equity
    if rest > 0:
        positions.append(Position("CASH", Holding.CASH, rest, value=rest))
    return PositionSnapshot(tuple(positions), as_of, "What-if", name)


# ----------------------------------------------------------------- builders

def scale_picks(weights: Mapping[str, float], core: Collection[str], total: float
                ) -> dict[str, float]:
    """The picks scaled together to ``total``, the core unchanged."""
    weights = validate(weights)
    core = {c.upper() for c in core}
    picks = {s: w for s, w in weights.items() if s not in core}
    held = sum(picks.values())
    if held <= 0:
        raise ValueError("there are no picks (holdings outside the core) to scale")
    if not 0.0 <= total <= 1.0:
        raise ValueError("a picks total is a share of equity between 0 and 100%")
    out = {s: (total * w / held if s in picks else w) for s, w in weights.items()}
    return validate(out)


def index_and_cash(index: str, share: float) -> dict[str, float]:
    return validate({index: share})


def net_into_core(picks: Mapping[str, float], betas: Mapping[str, float], core: str,
                  core_beta: float, target: float) -> dict[str, float]:
    """The picks as given, and the core at the weight that brings the book
    to β* (module docstring)."""
    picks = validate(picks)
    core = core.strip().upper()
    if core in picks:
        raise ValueError(f"{core} is the core, not a pick")
    missing = sorted(set(picks) - set(betas))
    if missing:
        raise ValueError(f"no β for {', '.join(missing)}")
    if not (math.isfinite(core_beta) and core_beta > 0 and math.isfinite(target)):
        raise ValueError("the core's β must be positive and the target finite")
    rest = sum(w * betas[s] for s, w in picks.items())
    weight = (target - rest) / core_beta
    if weight < 0:
        raise ValueError(f"the picks alone carry β {rest:.2f}, above the target {target:.2f}")
    if sum(picks.values()) + weight > 1.0 + _TOLERANCE:
        raise ValueError(f"β {target:.2f} needs a core of {weight:.1%}, which with the picks "
                         "passes 100% of equity: it needs leverage")
    return validate({**picks, core: weight})


# --------------------------------------------------------------- references

def _softmax(theta: np.ndarray, share: float) -> np.ndarray:
    z = np.concatenate(([0.0], theta))
    z = np.exp(z - z.max())
    return share * z / z.sum()


def _search(objective, n: int, share: float) -> np.ndarray:
    if n == 1:
        return np.array([share])
    result = nelder_mead(lambda theta: objective(_softmax(theta, share)), np.zeros(n - 1),
                         max_iterations=4_000 * n, initial_step=0.5, x_tolerance=1e-10,
                         f_tolerance=1e-14)
    return _softmax(result.x, share)


def _inputs(returns: pd.DataFrame, symbols: list[str]) -> np.ndarray:
    missing = [s for s in symbols if s not in returns.columns]
    if missing:
        raise ValueError(f"no returns for {', '.join(missing)}")
    frame = returns[symbols].dropna()
    if len(frame) < len(symbols) + 2:
        raise ValueError("too few days of returns to estimate the covariance")
    return frame.to_numpy(dtype=float)


def minimum_variance(returns: pd.DataFrame, symbols: list[str], share: float) -> dict[str, float]:
    x = _inputs(returns, symbols)
    cov = np.cov(x, rowvar=False, ddof=1).reshape(len(symbols), len(symbols))
    w = _search(lambda w: float(w @ cov @ w) / share ** 2, len(symbols), share)
    return validate(dict(zip(symbols, w)))


def equal_risk(returns: pd.DataFrame, symbols: list[str], share: float) -> dict[str, float]:
    x = _inputs(returns, symbols)
    cov = np.cov(x, rowvar=False, ddof=1).reshape(len(symbols), len(symbols))
    n = len(symbols)

    def gap(w: np.ndarray) -> float:
        total = float(w @ cov @ w)
        contributions = w * (cov @ w) / total
        return float(((contributions - 1.0 / n) ** 2).sum())
    return validate(dict(zip(symbols, _search(gap, n, share))))


def equal_weight(symbols: list[str], share: float) -> dict[str, float]:
    return validate({s: share / len(symbols) for s in symbols})


def max_sharpe(returns: pd.DataFrame, symbols: list[str], share: float,
               rate: float) -> dict[str, float]:
    """In-sample (module docstring): shown only with its warning."""
    x = _inputs(returns, symbols)
    cov = np.cov(x, rowvar=False, ddof=1).reshape(len(symbols), len(symbols))
    excess = x.mean(axis=0) - rate / TRADING_DAYS

    def negative_sharpe(w: np.ndarray) -> float:
        return -float(w @ excess) / math.sqrt(float(w @ cov @ w))
    return validate(dict(zip(symbols, _search(negative_sharpe, len(symbols), share))))
