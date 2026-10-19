r"""Daily long/cash research rules, fitted strictly before each test block (R2).

All signals at close t earn t -> t+1 in ``backtest``. Each asset receives
1/N of the available budget; inactive budgets remain cash. These are
transparent hypotheses, not optimized allocations or trading advice.

Momentum buys when log(P_t/P_{t-L}) > threshold (log-return units).
Mean reversion buys when z_t = (log P_t - trailing mean)/sample sd is
below -threshold. Rolling windows include the known close t.

Kalman trend buys when the forward-filtered slope divided by its state
standard deviation exceeds threshold. Level deviation buys when the
known log close lies threshold observation-noise standard deviations
below its filtered local level. A zero uncertainty gives no signal.
MLE parameters AND the initial state use training observations only;
test observations update the forward filter, never the smoother.

GARCH buys each asset, with risk sized from its one-step-ahead variance.
Markov buys if the filtered probability of the lowest-volatility regime
exceeds threshold. Its parameters use training only; the forward pass
continues from the same training history, never a smoothed regime.

Sizing is the homogeneous portfolio rule in ``portfolio.scale_to_risk``:
w = f b, f = min(target daily vol / sqrt(b' Sigma b), cap / ||b||_1).
For GARCH, convert the Gaussian log-return forecast (mean mu, variance h)
to simple-return variance (exp(h)-1) exp(2 mu+h), then use
Sigma = diag(forecast sigma) C_train diag(forecast sigma);
other rules use the trailing L daily simple-return sample covariance.
No positive estimated variance means cash, not infinite leverage. This
is a risk target, not a guarantee. Annualization is 252 sessions.

Training prices [start, t) exclude the first test close t. Refit blocks
are disjoint; warm-up decisions have zero weights and are excluded from
the service's performance window. Convergence and fitted parameters are
returned for the research log. No shorting, smoothing or hidden tuning.
"""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from quant_hub.models.backtest import validate_price_matrix
from quant_hub.models.garch import fit_garch11, next_variance
from quant_hub.models.kalman import fit_local_level, fit_local_linear_trend, kalman_filter
from quant_hub.models.markov import fit_hmm, hmm_filter
from quant_hub.models.portfolio import scale_to_risk

STRATEGIES = ("Momentum", "Mean reversion", "Kalman trend", "Kalman level",
              "GARCH volatility", "Markov calm")


@dataclass(frozen=True)
class StrategySpec:
    name: str = "Momentum"
    lookback: int = 63
    threshold: float = 0.0
    train_size: int = 252
    refit_every: int = 126
    target_vol: float = 0.15
    gross_cap: float = 1.0
    seed: int = 42

    def __post_init__(self):
        if self.name not in STRATEGIES:
            raise ValueError("unknown strategy")
        for name in ("lookback", "train_size", "refit_every", "seed"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < (0 if name == "seed" else 1):
                raise ValueError(f"{name} must be a positive integer (seed may be zero)")
        if self.lookback < 2 or self.train_size < max(30, self.lookback + 1):
            raise ValueError("training needs at least 30 prices and lookback + 1 prices")
        for name in ("threshold", "target_vol", "gross_cap"):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.target_vol <= 0 or not 0 < self.gross_cap <= 1:
            raise ValueError("target volatility must be positive; gross cap must be in (0, 1]")
        if self.name == "Markov calm" and not 0 <= self.threshold <= 1:
            raise ValueError("Markov threshold is a probability in [0, 1]")
        if self.name == "Markov calm" and self.train_size < 101:
            raise ValueError("Markov estimation needs at least 101 training prices")


@dataclass(frozen=True)
class StrategyResult:
    targets: pd.DataFrame
    fits: list[dict]
    first_decision: int


def strategy_targets(prices: pd.DataFrame, spec: StrategySpec) -> StrategyResult:
    """Pure prices/spec -> targets, with an auditable per-asset fit log."""
    validate_price_matrix(prices)
    if len(prices) < spec.train_size + 2:
        raise ValueError("need training prices plus at least two test prices")
    logp = np.log(prices.to_numpy())
    simple = prices.pct_change(fill_method=None).to_numpy()
    n, assets = prices.shape
    signals = np.zeros((n - 1, assets))
    forecast = np.zeros_like(signals)
    fits = []
    for t in range(spec.train_size, n - 1, spec.refit_every):
        stop = min(t + spec.refit_every, n - 1)
        start = t - spec.train_size
        for j, symbol in enumerate(prices.columns):
            train = logp[start:t, j]
            test = logp[t:stop, j]
            record = {"symbol": symbol, "train_start": str(prices.index[start].date()),
                      "train_end": str(prices.index[t - 1].date()),
                      "test_start": str(prices.index[t].date()),
                      "test_end": str(prices.index[stop - 1].date()), "converged": True}
            if spec.name.startswith("Kalman"):
                fit = (fit_local_linear_trend(train) if spec.name == "Kalman trend"
                       else fit_local_level(train))
                record.update(parameters=asdict(fit.parameters), converged=fit.converged,
                              zero_variances=list(fit.zero_variances))
                filtered = kalman_filter(fit.parameters.state_space(), test,
                                        fit.filtered.filtered_means[-1],
                                        fit.filtered.filtered_covariances[-1])
                if spec.name == "Kalman trend":
                    numerator = filtered.filtered_means[:, 1]
                    denominator = np.sqrt(np.maximum(filtered.filtered_covariances[:, 1, 1], 0))
                else:
                    numerator = filtered.filtered_means[:, 0] - test
                    denominator = np.full(len(test), np.sqrt(fit.parameters.noise_variance))
                z = np.divide(numerator, denominator, out=np.zeros(len(test)), where=denominator > 0)
                signals[t:stop, j] = (denominator > 0) & (z > spec.threshold)
            elif spec.name == "GARCH volatility":
                fit = fit_garch11(np.diff(train))
                record.update(parameters=asdict(fit.parameters), converged=fit.converged)
                variance = fit.conditional_variances[-1]
                previous = np.diff(train)[-1]
                for offset, value in enumerate(np.diff(logp[t - 1:stop, j])):
                    variance = next_variance(fit.parameters, previous, variance)
                    # variance is Var(r_t | t-1); observe r_t then forecast t+1.
                    h = next_variance(fit.parameters, value, variance)
                    forecast[t + offset, j] = np.expm1(h) * np.exp(2 * fit.parameters.mean + h)
                    previous = value
                signals[t:stop, j] = 1
            elif spec.name == "Markov calm":
                fit = fit_hmm(np.diff(train), 2, seed=spec.seed)
                record.update(parameters={"means": fit.model.means.tolist(),
                                          "sigmas": fit.model.sigmas.tolist(),
                                          "transition": fit.model.transition.tolist()},
                              converged=fit.converged, at_floor=list(fit.at_floor))
                # A fixed-parameter forward pass over train + observed test
                # continues the training state without inventing a new prior.
                filtered = hmm_filter(fit.model, np.diff(logp[start:stop, j])).filtered
                calm = int(np.argmin(fit.model.sigmas))
                signals[t:stop, j] = filtered[len(train) - 1:, calm] > spec.threshold
            else:
                for k in range(t, stop):
                    window = logp[k - spec.lookback + 1:k + 1, j]
                    if spec.name == "Momentum":
                        signals[k, j] = logp[k, j] - logp[k - spec.lookback, j] > spec.threshold
                    else:
                        sd = window.std(ddof=1)
                        signals[k, j] = sd > 0 and (window.mean() - window[-1]) / sd > spec.threshold
            fits.append(record)
        correlation = np.atleast_2d(np.corrcoef(simple[start + 1:t].T))
        correlation = np.nan_to_num(correlation)
        np.fill_diagonal(correlation, 1)
        for k in range(t, stop):
            covariance = np.atleast_2d(np.cov(simple[k - spec.lookback + 1:k + 1].T, ddof=1))
            if spec.name == "GARCH volatility":
                sd = np.sqrt(forecast[k])
                covariance = correlation * np.outer(sd, sd)
            signals[k] = scale_to_risk(signals[k] / assets, covariance,
                                       spec.target_vol / np.sqrt(252), spec.gross_cap)
    return StrategyResult(pd.DataFrame(signals, index=prices.index[:-1], columns=prices.columns),
                          fits, spec.train_size)
