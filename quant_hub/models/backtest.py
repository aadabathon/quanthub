r"""Daily, self-financing backtests with decisions explicitly lagged (R1).

Timing and units
----------------
``prices`` is a common-session close matrix, rows 0,...,n and one column
per asset. ``targets`` has rows 0,...,n-1: weights decided at close t,
using information through t, held for t -> t+1. Returned period returns
are dated t+1; trades and fees are dated t. There is no decision or forced
liquidation at the final close. The starting account is entirely cash.
Prices may be adjusted total-return indices; in that case holdings and
trades are economic exposures, not executable share counts.

The close fill is an idealized daily research convention (D33), not a
claim that a signal observing the final closing print can execute there.
There are no orders, intraday fills, market impact, taxes or borrow fees.
Cash earns an effective annual rate r converted to one session by
f = exp(log(1+r)/p)-1, p=252 by default. Negative cash borrows at that same
rate; shorts require ``allow_short`` and leverage requires a higher gross limit.

Accounting, derived before implementation
----------------------------------------
Let V_t be equity just before trading at close t; w_t the desired asset
weights **after fees**; u_t the asset weights before trading; and a_t the
fraction of V_t left after fees. A trade in asset i costs proportion c of
its absolute notional, where c=(half_spread_bps+commission_bps)/10,000.
The desired new position is a_t V_t w_ti; the old one is V_t u_ti. Thus

    turnover_t = sum_i |a_t w_ti - u_ti|,
    fee_t / V_t = c * turnover_t,
    a_t + c sum_i |a_t w_ti - u_ti| = 1.                 (1)

This pays fees from the portfolio itself. In particular, entering a fully
invested asset from cash gives a=1/(1+c), not 1-c with an unfunded holding.
Post-trade cash is a_t V_t (1-sum_i w_ti). Over the following session,

    G_(t+1) = 1 + sum_i w_ti R_(t+1,i) + (1-sum_i w_ti) f,
    V_(t+1) / V_t = a_t G_(t+1).                         (2)

Since the common a_(t-1) cancels when dividing holdings by equity, the
next pre-trade weights are

    u_ti = w_(t-1,i) (1+R_(t,i)) / G_t; u_0i = 0.        (3)

Consequently every row of (1) is independent: compute (3) from target
weights and returns, solve all rows of (1) together, then compound (2).
This is vectorized over time and assets; no sequential portfolio loop.
Unchanged target weights can still require trades after market drift.

With c*sum|w|<1, the left side of (1) is strictly increasing in a: its
piecewise slope is at least 1-c*sum|w|>0. At a=1 it is >=1. At a=0 it is
c*sum|u|, which must be <1 (otherwise liquidation alone exhausts equity).
There is therefore one solution in (0,1]. Bisection halves its [0,1]
bracket 48 times: the remaining equity-fraction uncertainty is <4e-15.
Zero-cost rows are exact and need no solve.

Reporting and limits
--------------------
``equity`` starts at initial equity immediately before the first decision;
later values are equity on arrival at each close, before its next trade.
The first return therefore includes the initial entry fee. ``gross_equity``
is the cost-free counterfactual using the same target-weight policy.
Fees and trades are actual dollars, turnover is total absolute traded
notional / pre-trade equity (no division by two). No sessions are filled or
dropped. The caller supplies an aligned session calendar and records price
basis/source; a missing observation is an error, not an implicit flat day.

``walk_forward_targets`` fits only on each training slice and calls the
predictor with copies of history through the decision close. It prevents
the runner from handing future bars to either callback; callers remain
responsible for callbacks' external data and state. Warm-up targets are
zero. Test folds do not overlap and the final fold can be shorter.

Validation: hand accounting, entry/exit and drift identities, a separate
share-and-cash ledger, zero-cost buy-and-hold, cash compounding, future
perturbation, exact callback windows, and input/solvency rejection.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from numbers import Integral

import numpy as np
import pandas as pd

from quant_hub.data.stats import TRADING_DAYS_PER_YEAR, drawdown_series

BASIS_POINTS = 10_000
_BISECTION_STEPS = 48       # [0,1] bracket width < 4e-15; see derivation
_WEIGHT_TOLERANCE = 1e-12   # round-off only, not extra permitted exposure


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 100_000.0
    half_spread_bps: float = 0.0
    commission_bps: float = 0.0
    cash_rate: float = 0.0
    periods_per_year: int = TRADING_DAYS_PER_YEAR
    max_gross_exposure: float = 1.0
    allow_short: bool = False

    def __post_init__(self) -> None:
        for name in ("initial_equity", "half_spread_bps", "commission_bps",
                     "cash_rate", "max_gross_exposure"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if self.initial_equity <= 0 or self.max_gross_exposure <= 0:
            raise ValueError("initial equity and gross exposure limit must be positive")
        if self.half_spread_bps < 0 or self.commission_bps < 0:
            raise ValueError("costs cannot be negative")
        if self.cash_rate <= -1:
            raise ValueError("cash_rate must exceed -1")
        _positive_integer(self.periods_per_year, "periods_per_year")
        if not isinstance(self.allow_short, bool):
            raise ValueError("allow_short must be boolean")
        if self.cost_rate * self.max_gross_exposure >= 1:
            raise ValueError("cost rate times gross exposure limit must be below one")

    @property
    def cost_rate(self) -> float:
        return (self.half_spread_bps + self.commission_bps) / BASIS_POINTS

    @property
    def cash_return(self) -> float:
        return math.expm1(math.log1p(self.cash_rate) / self.periods_per_year)


@dataclass(frozen=True)
class BacktestResult:
    config: BacktestConfig
    prices: pd.DataFrame
    targets: pd.DataFrame           # decision dates, n-1 rows
    pretrade_weights: pd.DataFrame  # same dates; drifted holdings before rebalance
    trades: pd.DataFrame            # signed dollars, same dates
    turnover: pd.Series             # absolute notional / pre-trade equity
    fees: pd.Series                 # dollars, decision dates
    gross_returns: pd.Series        # earning dates, n-1 rows
    net_returns: pd.Series          # includes fees at the preceding decision
    gross_equity: pd.Series         # n prices including the initial equity
    equity: pd.Series
    drawdown: pd.Series
    ending_weights: pd.Series      # final mark, no automatic liquidation


def backtest(prices: pd.DataFrame, targets: pd.DataFrame,
             config: BacktestConfig | None = None) -> BacktestResult:
    """Run the module's accounting. Targets use ``prices.index[:-1]`` exactly.

    Asset columns must match in name and order. Inputs are copied into the
    result; neither is sorted, aligned, filled, or mutated implicitly.
    """
    config = config or BacktestConfig()
    validate_price_matrix(prices)
    if not isinstance(targets, pd.DataFrame):
        raise ValueError("targets must be a DataFrame")
    if not targets.index.equals(prices.index[:-1]) or not targets.columns.equals(prices.columns):
        raise ValueError("targets need prices.index[:-1] and the same asset columns in order")
    w = targets.to_numpy(dtype=float, copy=True)
    if not np.isfinite(w).all():
        raise ValueError("targets must be finite; use explicit zero weights for warm-up")
    if not config.allow_short and (w < 0).any():
        raise ValueError("negative weights require allow_short=True")
    gross_weights = np.abs(w).sum(axis=1)
    if (gross_weights > config.max_gross_exposure + _WEIGHT_TOLERANCE).any():
        raise ValueError("targets exceed max_gross_exposure")
    if (config.cost_rate * gross_weights >= 1).any():
        raise ValueError("cost rate times actual gross exposure must be below one")

    values = prices.to_numpy(dtype=float)
    returns = values[1:] / values[:-1] - 1.0
    gross = 1.0 + np.sum(w * returns, axis=1) + (1.0 - w.sum(axis=1)) * config.cash_return
    if not np.isfinite(gross).all() or (gross <= 0).any():
        raise ValueError("a held portfolio exhausts equity; reduce exposure")
    before = np.zeros_like(w)
    before[1:] = w[:-1] * (1.0 + returns[:-1]) / gross[:-1, None]
    factors = _after_cost_fraction(w, before, config.cost_rate)
    trade_fractions = factors[:, None] * w - before
    turnover = np.abs(trade_fractions).sum(axis=1)
    net_factors = factors * gross
    equity = config.initial_equity * np.r_[1.0, np.cumprod(net_factors)]
    gross_equity = config.initial_equity * np.r_[1.0, np.cumprod(gross)]
    if (not np.isfinite(equity).all() or (equity <= 0).any()
            or not np.isfinite(gross_equity).all() or (gross_equity <= 0).any()):
        raise ValueError("equity overflow or underflow; shorten the window or reduce exposure")
    dates, columns = targets.index, prices.columns
    path = pd.Series(equity, index=prices.index, name="equity")
    return BacktestResult(
        config=config, prices=prices.copy(), targets=targets.copy(),
        pretrade_weights=pd.DataFrame(before, index=dates, columns=columns),
        trades=pd.DataFrame(trade_fractions * equity[:-1, None], index=dates, columns=columns),
        turnover=pd.Series(turnover, index=dates, name="turnover"),
        fees=pd.Series(config.cost_rate * turnover * equity[:-1], index=dates, name="fees"),
        gross_returns=pd.Series(gross - 1.0, index=prices.index[1:], name="gross_return"),
        net_returns=pd.Series(net_factors - 1.0, index=prices.index[1:], name="net_return"),
        gross_equity=pd.Series(gross_equity, index=prices.index, name="gross_equity"),
        equity=path, drawdown=drawdown_series(path),
        ending_weights=pd.Series(w[-1] * (1.0 + returns[-1]) / gross[-1],
                                 index=columns, name="ending_weight"),
    )


def _after_cost_fraction(weights: np.ndarray, before: np.ndarray, rate: float) -> np.ndarray:
    if rate == 0:
        return np.ones(len(weights))
    if (rate * np.abs(before).sum(axis=1) >= 1).any():
        raise ValueError("trading costs exhaust equity even on liquidation")
    low, high = np.zeros(len(weights)), np.ones(len(weights))
    for _ in range(_BISECTION_STEPS):
        mid = (low + high) / 2
        residual = mid + rate * np.abs(mid[:, None] * weights - before).sum(axis=1) - 1
        low = np.where(residual < 0, mid, low)
        high = np.where(residual >= 0, mid, high)
    # Rows requiring no trade should leave equity exactly unchanged.
    result = (low + high) / 2
    return np.where(np.all(weights == before, axis=1), 1.0, result)


@dataclass(frozen=True)
class WalkForwardSplit:
    train: slice
    test: slice


def walk_forward_splits(n_decisions: int, train_size: int, test_size: int,
                        *, expanding: bool = False) -> tuple[WalkForwardSplit, ...]:
    """Disjoint test blocks in decision-row coordinates, half-open slices.

    Each training window ends before its first test decision. ``train_size``
    is a number of close observations, not returns (one fewer return).
    The last block is retained even when shorter than ``test_size``.
    """
    for name, value in (("n_decisions", n_decisions), ("train_size", train_size),
                        ("test_size", test_size)):
        _positive_integer(value, name)
    if train_size < 2 or train_size >= n_decisions:
        raise ValueError("train_size must be at least two and leave test decisions")
    if not isinstance(expanding, bool):
        raise ValueError("expanding must be boolean")
    return tuple(WalkForwardSplit(slice(0 if expanding else cut - train_size, cut),
                                   slice(cut, min(cut + test_size, n_decisions)))
                 for cut in range(train_size, n_decisions, test_size))


def walk_forward_targets(prices: pd.DataFrame, fit: Callable[[pd.DataFrame], object],
                         predict: Callable[[object, pd.DataFrame], pd.Series],
                         train_size: int, test_size: int, *, expanding: bool = False
                         ) -> pd.DataFrame:
    """Fit each training block, then predict one close at a time without future bars.

    ``predict(model, history_through_t)`` returns a finite weight Series with
    exactly the price columns. Exposure limits are checked by ``backtest``.
    A fitted model may update its own filtering state while processing the
    test block; this function never refits on its future test observations.
    """
    validate_price_matrix(prices)
    splits = walk_forward_splits(len(prices) - 1, train_size, test_size, expanding=expanding)
    weights = pd.DataFrame(0.0, index=prices.index[:-1], columns=prices.columns)
    for split in splits:
        model = fit(prices.iloc[split.train].copy())
        for row in range(split.test.start, split.test.stop):
            decision = predict(model, prices.iloc[:row + 1].copy())
            if not isinstance(decision, pd.Series) or not decision.index.equals(prices.columns):
                raise ValueError("predict must return a Series with the price columns in order")
            if not np.isfinite(decision.to_numpy(dtype=float)).all():
                raise ValueError("predict returned non-finite weights")
            weights.iloc[row] = decision.to_numpy(dtype=float)
    return weights


def _positive_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def validate_price_matrix(prices: pd.DataFrame) -> None:
    if not isinstance(prices, pd.DataFrame) or len(prices) < 2 or prices.shape[1] < 1:
        raise ValueError("prices need at least two closes and one asset")
    index = prices.index
    if (not isinstance(index, pd.DatetimeIndex) or index.hasnans or index.has_duplicates
            or not index.is_monotonic_increasing or index.tz is not None
            or not index.equals(index.normalize())):
        raise ValueError("prices need sorted, unique, naive daily session dates")
    if prices.columns.has_duplicates or any(not isinstance(c, str) or not c for c in prices.columns):
        raise ValueError("asset columns must be unique nonempty strings")
    values = prices.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("prices must be finite and positive on every supplied session")
