"""The regime evidence run (REGIME_MODEL Q3c, D81).

Builds every path ``models/regime_evidence.py`` pre-registers, from the
stored series, and runs its two shift tests. Then it keeps the inputs the
evidence keeps, and saves the evidence as a Research recorded run (Q3a).

**The steps:**
1. Q1's inputs, as known at the time: about four minutes for the
   walk-forward refits.
2. October's 31 rules, ported (``models/timing_rules``), on French's
   market and Shiller's valuations with that session's lags.
3. Q2's μ̂ for each variant:
   - the full model F
   - F without each input
   - each input alone
   - the historical mean alone, which with σ̂ is vol targeting (B1)

   Each becomes a daily β path through the provisional growth rule,
   unclipped at f = 1.
4. Each input's difference path Δ_j = β^F − β^{F−j}.
5. The family-wise shift test over all of them, on their common days.
6. The tail: each input's own forecast and the pool, at each threshold,
   and the tail's shift test.
7. **What's kept:**
   - a μ̂ input whose Δ_j clears the bar (decision 6), and whose α stays
     positive after its paper's year
   - a tail input whose forecast beats the base rate, clears the bar, and
     whose skill stays positive after its paper's year
8. **The saved run:**
   - **Its decisions** are the kept model's provisional path (f = ½, no
     leverage), on French's market, with RF as cash.
   - **Its series** are the kept inputs.
   - **Its details** hold the whole ledger.

Blocking, about five minutes: Ctrl+K's "Run the regime evidence" runs it on
the TaskRunner.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_hub.models.backtest import BacktestConfig
from quant_hub.models.regime_evidence import (
    BAR,
    CRISES,
    ERAS,
    GROWTH_CAP,
    GROWTH_F,
    PUBLISHED,
    SEED,
    SHIFTS,
    ShiftTest,
    TailShiftTest,
    after_year,
    brier_skill,
    crisis_drawdowns,
    growth_gain,
    growth_rule,
    max_drawdown,
    on_days,
    portfolio_returns,
    shift_test,
    tail_shift_test,
    timing_alpha,
)
from quant_hub.models.regime_inputs import (
    REFIT_YEARS,
    excess_cape_yield,
    expanding_percentile,
    garch_vol_ahead,
    real_time_cape,
)
from quant_hub.models.regime_model import (
    MIN_EPISODES,
    MIN_ORIGINS,
    THRESHOLDS,
    inputs_at,
    mean_forecast,
    origins,
    r2_out_of_sample,
    tail_forecast,
)
from quant_hub.models.timing_rules import MIN_MONTHS, SPIKE_LAGS, family
from quant_hub.services.external_data import ExternalDataService
from quant_hub.services.regime_inputs import (
    BILLS,
    MARKET,
    SHILLER,
    RegimeInputSet,
    load_regime_inputs,
)
from quant_hub.services.research import ResearchService

_logger = logging.getLogger(__name__)
LEDGER = "regime"
COSTS = BacktestConfig(half_spread_bps=1, commission_bps=1, max_gross_exposure=GROWTH_CAP)


@dataclass(frozen=True)
class EvidenceRun:
    family: ShiftTest
    tail: TailShiftTest
    ledger: tuple[dict, ...]
    kept_mean: tuple[str, ...]
    kept_tail: tuple[str, ...]
    paths: pd.DataFrame          # the family's daily β paths
    run_id: str | None
    notes: tuple[str, ...]


def _digest(path: pd.Series) -> str:
    values = np.round(path.to_numpy(dtype=float), 12)
    return hashlib.sha256(np.nan_to_num(values, nan=-1e300).tobytes()).hexdigest()


def _spike_percentiles(shiller: dict[str, pd.Series], days: pd.DatetimeIndex):
    """October's CAPE and ECY percentiles, on the market's days."""
    out = []
    for raw in (real_time_cape(shiller["price"], shiller["earnings"], shiller["cpi"],
                               SPIKE_LAGS),
                excess_cape_yield(shiller["price"], shiller["earnings"], shiller["cpi"],
                                  shiller["gs10"], SPIKE_LAGS)):
        pct = pd.Series(expanding_percentile(raw.to_numpy(), MIN_MONTHS), index=raw.index)
        out.append(on_days(pct, days))
    return out


def run_evidence(external: ExternalDataService, research: ResearchService | None = None, *,
                 inputs: RegimeInputSet | None = None, draws: int = SHIFTS, seed: int = SEED,
                 refit_years: int = REFIT_YEARS, min_origins: int = MIN_ORIGINS,
                 min_episodes: int = MIN_EPISODES, min_shift_days: int = 252 * 5,
                 min_shift_months: int = 60,
                 progress: Callable[[str], None] = _logger.info) -> EvidenceRun:
    """Blocking: the whole evidence run (module docstring)."""
    progress("Regime evidence: building the inputs as known at the time…")
    inputs = inputs or load_regime_inputs(external, refit_years=refit_years)
    if inputs.walk is None or len(inputs.inputs) < 2:
        raise ValueError("the evidence needs every input: " + " ".join(inputs.missing))
    excess = external.series(*MARKET).values
    bills = external.series(*BILLS).values.reindex(excess.index)
    total = (excess + bills).dropna()
    excess, bills = excess.reindex(total.index), bills.reindex(total.index)
    days = total.index
    shiller = {key: external.series("shiller", sid).values for key, sid in SHILLER.items()}
    notes: list[str] = []

    progress("Regime evidence: October's 31 rules and Q2's forecasts…")
    cape_pct, ecy_pct = _spike_percentiles(shiller, days)
    october = family(total, cape_pct, ecy_pct)
    o = origins(total, bills)
    X = inputs_at(o, inputs.inputs)
    names = list(X.columns)
    sigma_21 = garch_vol_ahead(inputs.walk, 21).reindex(days)
    sigma_252 = garch_vol_ahead(inputs.walk, 252).reindex(days)

    def path(mean_monthly: pd.Series, f: float = 1.0, cap: float | None = None) -> pd.Series:
        return growth_rule(on_days(mean_monthly, days), sigma_21, sigma_252, f, cap)

    forecasts = {"F": mean_forecast(o, X, min_origins=min_origins)}
    for name in names:
        forecasts[f"F−{name}"] = mean_forecast(o, X.drop(columns=[name]), min_origins=min_origins)
        forecasts[f"S {name}"] = mean_forecast(o, X[[name]], min_origins=min_origins)
    benchmark = forecasts["F"].benchmark
    rules = {"B1 vol targeting": path(benchmark),
             **{key: path(mf.forecast) for key, mf in forecasts.items()}}
    differences = {f"Δ {name}": rules["F"] - rules[f"F−{name}"] for name in names}
    paths = pd.DataFrame({**{f"Oct: {k}": v for k, v in october.items()}, **rules,
                          **differences}, index=days)

    progress(f"Regime evidence: the family-wise shift test, {paths.shape[1]} paths × "
             f"{draws:,} shifts…")
    test = shift_test(paths, excess, draws, min_shift_days, seed)
    first, last = test.first, test.last

    progress("Regime evidence: the tail's forecasts and shift test…")
    tails = {f"S {name}": tail_forecast(o, X[[name]], min_origins=min_origins,
                                        min_episodes=min_episodes) for name in names}
    tails["pool"] = tail_forecast(o, X, min_origins=min_origins, min_episodes=min_episodes)
    tail_inputs = {}
    for key, tf in tails.items():
        for d in THRESHOLDS:
            events = (o.drawdown >= d).astype(float).where(o.drawdown.notna())
            tail_inputs[f"{key} @{d:.0%}"] = (tf.probability[d], tf.base_rate[d], events)
    tail = tail_shift_test(tail_inputs, draws, min_shift_months, seed)

    progress("Regime evidence: the measures and the ledger…")
    ledger: list[dict] = []
    for name in paths.columns:
        kind = ("october" if name.startswith("Oct: ") else
                "difference" if name.startswith("Δ ") else "rule")
        entry = {"id": name, "kind": kind, "path_sha256": _digest(paths[name]),
                 **test.row(name)}
        whole = timing_alpha(paths[name], excess, first, last)
        entry.update(alpha=whole.alpha, error=whole.error, days=whole.days)
        entry["eras"] = {label: _alpha_row(paths[name], excess, max(first, _ts(a, first)),
                                           min(last, _ts(b, last)))
                         for label, a, b in ERAS}
        if kind == "difference":
            input_name = name[2:]
            year = PUBLISHED.get(input_name)
            entry["published"] = year
            entry["after_publication"] = (_alpha_row(paths[name], excess, after_year(year), last)
                                          if year else None)
        else:
            grown = (path(forecasts[name].forecast if name in forecasts else benchmark,
                          GROWTH_F, GROWTH_CAP) if kind == "rule" else paths[name])
            gain, gain_error = growth_gain(grown, total, bills, first, last)
            returns = portfolio_returns(grown, total, bills).loc[first:last]
            entry.update(growth=gain, growth_error=gain_error,
                         max_drawdown=max_drawdown(returns) if len(returns) else None,
                         crises=crisis_drawdowns({name: returns}, total)[name].to_dict()
                         if len(returns) else {})
            if name in forecasts:
                entry["r2_out_of_sample"] = r2_out_of_sample(o.y, forecasts[name].forecast,
                                                             forecasts[name].benchmark)
        ledger.append(entry)

    kept_mean = tuple(
        name for name in names
        if (row := next(e for e in ledger if e["id"] == f"Δ {name}"))["adjusted"] <= BAR
        and (row["after_publication"] is None or row["after_publication"]["alpha"] > 0))
    tail_rows = {}
    for key in tail.names:
        source, _threshold = key.rsplit(" @", 1)
        p, base, events = tail_inputs[key]
        row = {"id": key, "kind": "tail", **tail.row(key)}
        input_name = source[2:] if source.startswith("S ") else None
        year = PUBLISHED.get(input_name) if input_name else None
        if year:
            late = pd.concat([p, base, events], axis=1).dropna().loc[after_year(year):]
            row["after_publication"] = (float(brier_skill(late.iloc[:, [0]].to_numpy(),
                                                          late.iloc[:, 1].to_numpy(),
                                                          late.iloc[:, 2].to_numpy())[0])
                                        if len(late) > 12 else None)
        tail_rows[key] = row
        ledger.append(row)
    kept_tail = tuple(
        name for name in names
        if any(row["skill"] > 0 and row["adjusted"] <= BAR
               and (row.get("after_publication") is None or row["after_publication"] > 0)
               for key, row in tail_rows.items() if key.startswith(f"S {name} @")))
    if not kept_mean:
        notes.append("No μ̂ input cleared the bar: the kept model's μ̂ is the historical "
                     "mean, so its path is vol targeting.")

    run_id = None
    if research is not None:
        kept_forecast = (mean_forecast(o, X[list(kept_mean)], min_origins=min_origins).forecast
                         if kept_mean else benchmark)
        decision = path(kept_forecast, GROWTH_F, GROWTH_CAP).fillna(0.0)
        prices = pd.DataFrame({"MARKET": 100 * (1 + total).cumprod()}, index=days)
        targets = pd.DataFrame({"MARKET": decision.iloc[:-1].to_numpy()}, index=days[:-1])
        series = {i.name: i.raw for i in inputs.inputs if i.name in kept_mean + kept_tail}
        run = research.record(
            "Regime evidence · French market " + f"{first:%Y}–{last:%Y}", prices, targets,
            costs=COSTS, cash=bills.iloc[1:], series=series,
            provenance={"source": "french + shiller", "basis": "total return",
                        "series": list(inputs.sources)},
            details={"model": "the regime model's kept inputs, provisional growth rule "
                              f"(f = {GROWTH_F}, β ≤ {GROWTH_CAP})",
                     "ledger_name": LEDGER, "ledger": ledger, "kept_mean": list(kept_mean),
                     "kept_tail": list(kept_tail),
                     "family": {"paths": len(test.names), "draws": test.draws, "seed": seed,
                                "p_family": test.p_family, "best": test.names[
                                    int(np.argmax(test.t))], "first": first.isoformat(),
                                "last": last.isoformat(), "days": test.days},
                     "tail": {"forecasts": len(tail.names), "origins": tail.months,
                              "draws": tail.draws},
                     "bar": BAR, "crises": [c[0] for c in CRISES], "notes": notes},
            hypothesis="Each input stays only if its timing clears a family-wise p of 0.05 "
                       "and holds after its paper's year (REGIME_MODEL decision 6).")
        run_id = run.id
    progress(f"Regime evidence: done. Kept for μ̂: {', '.join(kept_mean) or 'none'}; "
             f"for the tail: {', '.join(kept_tail) or 'none'}.")
    return EvidenceRun(test, tail, tuple(ledger), kept_mean, kept_tail, paths, run_id,
                       tuple(notes))


def _ts(value, default: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(value) if value is not None else default


def _alpha_row(beta: pd.Series, excess: pd.Series, start, end) -> dict | None:
    fit = timing_alpha(beta, excess, start, end)
    if not np.isfinite(fit.alpha):
        return None
    return {"alpha": fit.alpha, "error": fit.error, "days": fit.days}

