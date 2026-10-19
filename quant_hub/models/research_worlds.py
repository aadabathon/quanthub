r"""Seeded research controls, not market forecasts (R2).

Null: P_(t+1) = P_t exp(-sigma²/2 + sigma Z), sigma=.01 daily.
Thus E[P_(t+1)/P_t | history] = 1: a long/cash timing rule has zero
expected gross return. Costs reduce it. Trading sessions are synthetic.

Trend: log-return r_(t+1)=phi r_t + sigma Z, phi=.6, sigma=.008.
Initialize r_0 from N(0, sigma²/(1-phi²)) for stationarity. Then
E[r_(t+1) 1(r_t>0)] = phi sigma / sqrt(2 pi (1-phi²)) > 0;
this plants a predictable log-return edge for short-horizon momentum.

Reversion: log-price deviation y_(t+1)=.95 y_t + .01 Z, initialized
from its stationary Gaussian; P_t=100 exp(y_t). E[delta y | y_t]
= -.05 y_t, an explicit contrarian edge. Assets have independent shocks.
All constants are scenario parameters, deliberately not calibrated.
"""
import numpy as np
import pandas as pd

WORLDS = ("Null random walk", "Planted trend", "Mean-reverting level")


def research_world(kind: str, observations: int = 1000, seed: int = 42,
                   symbols: tuple[str, ...] = ("WORLD",)) -> pd.DataFrame:
    if kind not in WORLDS:
        raise ValueError("unknown research world")
    if not isinstance(observations, int) or observations < 3:
        raise ValueError("observations must be an integer >= 3")
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be nonempty and unique")
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((observations, len(symbols)))
    if kind == WORLDS[0]:
        increments = -.01**2 / 2 + .01 * z[1:]
        values = np.vstack([np.zeros(len(symbols)), np.cumsum(increments, axis=0)])
    else:
        phi, sigma = (.6, .008) if kind == WORLDS[1] else (.95, .01)
        values = np.empty_like(z)
        values[0] = sigma / np.sqrt(1 - phi**2) * z[0]
        for t in range(1, observations):
            values[t] = phi * values[t - 1] + sigma * z[t]
        if kind == WORLDS[1]:
            values = np.cumsum(values, axis=0)
            values -= values[0]
    return pd.DataFrame(100 * np.exp(values), columns=symbols,
                        index=pd.bdate_range("2020-01-02", periods=observations))
