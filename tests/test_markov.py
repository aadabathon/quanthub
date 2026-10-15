"""Gaussian hidden Markov model (M1): exact against brute-force enumeration,
EM's guarantees, and statistical checks against the model's closed forms
(the validation plan in the module docstring)."""

import itertools
import math

import numpy as np
import pytest

from quant_hub.models.markov import (
    GaussianHMM,
    _em,
    fit_hmm,
    hmm_filter,
    hmm_smoother,
    regime_forecast,
    select_regimes,
    simulate_hmm,
    viterbi,
)

TWO = GaussianHMM([0.0005, -0.001], [0.007, 0.02], [[0.98, 0.02], [0.05, 0.95]])
THREE = GaussianHMM([0.0006, 0.0, -0.002], [0.005, 0.012, 0.03],
                    [[0.98, 0.015, 0.005], [0.03, 0.95, 0.02], [0.02, 0.08, 0.90]])
IID = GaussianHMM([0.0003], [0.01], [[1.0]])


def random_model(k: int, rng: np.random.Generator) -> GaussianHMM:
    return GaussianHMM(rng.normal(0.0, 0.005, k), rng.uniform(0.005, 0.03, k),
                       rng.dirichlet(np.full(k, 2.0), size=k))


def log_emissions(model, r):
    z = (r[:, None] - model.means) / model.sigmas
    return -0.5 * z * z - np.log(model.sigmas) - 0.5 * math.log(2 * math.pi)


def brute_force(model: GaussianHMM, r: np.ndarray) -> dict:
    """Every quantity by enumerating all K^n regime paths."""
    k, n = model.n_regimes, len(r)
    b = np.exp(log_emissions(model, r))
    p, pi = model.transition, model.initial

    def weight(path, emitted):          # joint density of the path and r[:emitted]
        w = pi[path[0]]
        for t, s in enumerate(path):
            if t:
                w *= p[path[t - 1], s]
            if t < emitted:
                w *= b[t, s]
        return w

    filtered, predicted = np.zeros((n, k)), np.zeros((n, k))
    for t in range(n):
        for path in itertools.product(range(k), repeat=t + 1):
            filtered[t, path[-1]] += weight(path, t + 1)
            predicted[t, path[-1]] += weight(path, t)
    paths = list(itertools.product(range(k), repeat=n))
    weights = np.array([weight(path, n) for path in paths])
    total = weights.sum()
    smoothed, counts = np.zeros((n, k)), np.zeros((k, k))
    for path, w in zip(paths, weights):
        for t in range(n):
            smoothed[t, path[t]] += w / total
            if t + 1 < n:
                counts[path[t], path[t + 1]] += w / total
    return {"log_likelihood": math.log(total),
            "filtered": filtered / filtered.sum(axis=1, keepdims=True),
            "predicted": predicted / predicted.sum(axis=1, keepdims=True),
            "smoothed": smoothed, "counts": counts,
            "viterbi": np.array(paths[int(np.argmax(weights))])}


def log_space_log_likelihood(model, r):
    """The forward pass done entirely in logs: slow, and immune to underflow."""
    log_b = log_emissions(model, r)
    with np.errstate(divide="ignore"):
        log_p = np.log(model.transition)
        x = np.log(model.initial) + log_b[0]
    for t in range(1, len(r)):
        x = np.logaddexp.reduce(x[:, None] + log_p, axis=0) + log_b[t]
    return float(np.logaddexp.reduce(x))


class TestExactness:
    @pytest.mark.parametrize("k, seed", [(2, 0), (2, 1), (3, 2), (3, 3)])
    def test_filter_smoother_and_viterbi_equal_brute_force(self, k, seed):
        rng = np.random.default_rng(seed)
        model = random_model(k, rng)
        r = rng.normal(0.0, 0.012, 7)
        truth = brute_force(model, r)
        filtered = hmm_filter(model, r)
        smoothed = hmm_smoother(filtered)
        assert filtered.log_likelihood == pytest.approx(truth["log_likelihood"], rel=1e-12)
        np.testing.assert_allclose(filtered.filtered, truth["filtered"], rtol=1e-10, atol=1e-14)
        np.testing.assert_allclose(filtered.predicted, truth["predicted"], rtol=1e-10, atol=1e-14)
        np.testing.assert_allclose(smoothed.smoothed, truth["smoothed"], rtol=1e-10, atol=1e-14)
        np.testing.assert_allclose(smoothed.transition_counts, truth["counts"], rtol=1e-10,
                                   atol=1e-14)
        np.testing.assert_array_equal(viterbi(model, r), truth["viterbi"])

    def test_an_extreme_day_leaves_the_likelihood_finite(self):
        # -20% among 1% days: every emission is ~e^-800, zero in double precision.
        model = GaussianHMM([0.0, 0.0], [0.005, 0.01], [[0.9, 0.1], [0.1, 0.9]])
        r = np.random.default_rng(4).normal(0.0, 0.01, 50)
        r[25] = -0.20
        got = hmm_filter(model, r).log_likelihood
        assert math.isfinite(got)
        assert got == pytest.approx(log_space_log_likelihood(model, r), rel=1e-12)

    def test_a_day_whose_best_regime_is_impossible_is_redone_in_logs(self):
        # Day 1 leaves regime 1 at e^-500000, zero in double precision, and
        # regime 0 is absorbing. Day 2 fits only regime 1, so the shifted
        # evidence underflows to zero; without the log-space redo, log L
        # would be -inf. The answer is the one path a double can still
        # hold, all regime 0. (The exact log-space answer also counts the
        # e^-500000 paths through regime 1, and differs by log 1.25.)
        model = GaussianHMM([0.0, 1.0], [0.001, 0.001], [[1.0, 0.0], [0.5, 0.5]])
        r = np.array([0.0, 1.0, 0.0])
        result = hmm_filter(model, r)
        regime_0 = log_emissions(model, r)[:, 0]
        assert result.log_likelihood == pytest.approx(math.log(0.5) + regime_0.sum(), rel=1e-15)
        np.testing.assert_array_equal(result.filtered, [[1.0, 0.0]] * 3)


class TestCalibration:
    def test_filtered_and_smoothed_probabilities_are_calibrated(self):
        # Under the true model, of the days given probability ~p of the
        # turbulent regime, a share ~p are turbulent. Persistent regimes
        # correlate the errors, so the binomial SE understates the noise:
        # 4 seeds measured a worst decile error of 0.017 (filtered) and
        # 0.028 (smoothed). A transposed P (a real bug) scores 0.27.
        sim = simulate_hmm(TWO, 200_000, seed=0)
        filtered = hmm_filter(TWO, sim.returns)
        for p in (filtered.filtered[:, 1], hmm_smoother(filtered).smoothed[:, 1]):
            deciles = np.minimum((p * 10).astype(int), 9)
            for d in range(10):
                days = deciles == d
                if days.sum() >= 1000:
                    assert abs((sim.states[days] == 1).mean() - p[days].mean()) < 0.04


@pytest.fixture(scope="module")
def sweeps():
    """One BIC sweep per synthetic truth, shared by the estimation tests:
    fits are the slow part (2–5 s a sweep). IID gets candidates (1, 2):
    a 3-regime fit there is unidentified and runs out its iterations (see
    test_too_many_regimes_shows_as_not_converged), 6 s of suite time."""
    cases = {"iid": (IID, 2500, (1, 2)), "two": (TWO, 2500, (1, 2, 3)),
             "three": (THREE, 5000, (1, 2, 3))}
    out = {}
    for name, (model, n, candidates) in cases.items():
        sim = simulate_hmm(model, n, seed=200)
        out[name] = (model, sim, select_regimes(sim.returns, candidates))
    return out


class TestEstimation:
    @pytest.mark.parametrize("case", ["two", "three"])
    def test_fit_recovers_the_truth_and_beats_its_likelihood(self, sweeps, case):
        model, sim, selection = sweeps[case]
        fit = selection.fits[model.n_regimes - 1]
        assert fit.converged
        # The search found the maximum, not just a nearby point (the C3 lesson).
        assert fit.log_likelihood >= hmm_filter(model, sim.returns).log_likelihood - 1e-6
        # Errors in standard errors that treat the regimes as known; regime
        # uncertainty inflates the true ones, most for the means. Measured
        # over 14 seeded fits: sigma at most 2.2, p_ii 3.6, mu 4.1.
        days = model.stationary_distribution * len(sim.returns)
        stay = np.diag(model.transition)
        assert np.all(np.abs(fit.model.sigmas - model.sigmas)
                      <= 3.0 * model.sigmas / np.sqrt(2 * days))
        assert np.all(np.abs(np.diag(fit.model.transition) - stay)
                      <= 4.0 * np.sqrt(stay * (1 - stay) / days))
        assert np.all(np.abs(fit.model.means - model.means) <= 5.0 * model.sigmas / np.sqrt(days))

    def test_em_never_decreases_the_likelihood(self, sweeps):
        for _model, sim, selection in sweeps.values():
            for fit in selection.fits[1:]:          # K = 1 is closed form, no EM
                assert np.diff(fit.log_likelihood_path).min() >= -1e-9
        # And from a poor start, where the climb is long.
        r = sweeps["three"][1].returns[:1000]
        _model, path, _converged = _em(r, random_model(3, np.random.default_rng(6)),
                                       0.01 * r.var(), 100)
        assert np.diff(path).min() >= -1e-9

    @pytest.mark.parametrize("case", ["iid", "two", "three"])
    def test_bic_picks_the_true_number_of_regimes(self, sweeps, case):
        model, _sim, selection = sweeps[case]
        assert selection.chosen.model.n_regimes == model.n_regimes

    def test_too_many_regimes_shows_as_not_converged(self):
        # Extra regimes on i.i.d. returns are unidentified: EM crawls along a
        # flat ridge, and the fit says it stopped rather than converged.
        r = simulate_hmm(IID, 2500, seed=200).returns
        fit = fit_hmm(r, 3, max_iterations=60)
        assert not fit.converged and fit.iterations == 60
        assert fit.log_likelihood_path[-1] - fit.log_likelihood_path[-2] > 1e-6

    def test_the_floor_stops_a_regime_collapsing_onto_zero_returns(self):
        # Stale prices: 30% of days return exactly zero. Without the floor a
        # regime sits on them with sigma -> 0 and log L -> infinity.
        rng = np.random.default_rng(3)
        r = 0.01 * rng.standard_normal(1000)
        r[rng.random(1000) < 0.3] = 0.0
        unfloored, path, _ = _em(r, random_model(2, np.random.default_rng(13)), 1e-300, 200)
        assert unfloored.sigmas.min() < 1e-100 and path[-1] > 1e5
        fit = fit_hmm(r, 2)
        assert fit.converged and math.isfinite(fit.log_likelihood)
        assert fit.at_floor == (0,)
        assert np.all(fit.model.sigmas**2 >= fit.variance_floor * (1 - 1e-12))
        assert np.diff(fit.log_likelihood_path).min() >= -1e-9

    def test_labels_are_sorted_and_free_of_the_start_labelling(self, sweeps):
        for _model, _sim, selection in sweeps.values():
            for fit in selection.fits:
                assert np.all(np.diff(fit.model.sigmas) > 0)
        r = sweeps["three"][1].returns[:1000]
        start = random_model(3, np.random.default_rng(8))
        floor = 0.01 * r.var()
        a, _, _ = _em(r, start, floor, 40)
        b, _, _ = _em(r, start.permuted([2, 0, 1]), floor, 40)
        a, b = a.ordered(), b.ordered()
        for name in ("means", "sigmas", "transition"):
            np.testing.assert_allclose(getattr(a, name), getattr(b, name), rtol=1e-7, atol=1e-12)

    def test_one_regime_is_the_closed_form(self):
        r = simulate_hmm(IID, 500, seed=9).returns
        fit = fit_hmm(r, 1)
        assert fit.model.means[0] == pytest.approx(r.mean())
        assert fit.model.sigmas[0] == pytest.approx(r.std())
        expected = float(np.sum(-0.5 * ((r - r.mean()) / r.std()) ** 2 - math.log(r.std())
                                - 0.5 * math.log(2 * math.pi)))
        assert fit.log_likelihood == pytest.approx(expected, rel=1e-12)
        assert fit.n_parameters == 2

    def test_fits_are_reproducible(self):
        r = simulate_hmm(TWO, 1500, seed=10).returns
        a, b = fit_hmm(r, 2), fit_hmm(r, 2)
        np.testing.assert_array_equal(a.model.transition, b.model.transition)
        assert a.log_likelihood == b.log_likelihood


class TestWhatTheModelImplies:
    @pytest.fixture(scope="class")
    def long_run(self):
        return simulate_hmm(TWO, 400_000, seed=11)

    def test_stationary_distribution_and_durations(self):
        pi = THREE.stationary_distribution
        np.testing.assert_allclose(pi @ THREE.transition, pi, atol=1e-14)
        assert pi.sum() == pytest.approx(1.0)
        p12, p21 = TWO.transition[0, 1], TWO.transition[1, 0]
        np.testing.assert_allclose(TWO.stationary_distribution, [p21, p12] / np.float64(p12 + p21))
        np.testing.assert_allclose(TWO.expected_durations, [50.0, 20.0])
        assert IID.expected_durations[0] == math.inf

    def test_simulated_occupancy_matches_the_stationary_distribution(self, long_run):
        pi = TWO.stationary_distribution
        lam = TWO.transition[0, 0] + TWO.transition[1, 1] - 1
        # SE of a share of correlated days: the binomial SE inflated by the
        # chain's (1 + lambda)/(1 - lambda).
        se = math.sqrt(pi[0] * pi[1] / len(long_run.states) * (1 + lam) / (1 - lam))
        assert abs((long_run.states == 1).mean() - pi[1]) < 4 * se

    def test_mean_stays_match_expected_durations(self, long_run):
        s = long_run.states
        starts = np.flatnonzero(np.diff(np.r_[-1, s]) != 0)
        lengths = np.diff(np.r_[starts, len(s)])
        for regime, expected in zip(range(2), TWO.expected_durations):
            stays = lengths[s[starts] == regime][1:-1]      # drop the cut-off ends
            p = TWO.transition[regime, regime]
            se = math.sqrt(p) / (1 - p) / math.sqrt(len(stays))   # geometric SD / sqrt(count)
            assert abs(stays.mean() - expected) < 4 * se

    def test_squared_returns_cluster_as_lambda_to_the_h(self, long_run):
        pi = TWO.stationary_distribution
        lam = TWO.transition[0, 0] + TWO.transition[1, 1] - 1
        v = TWO.sigmas**2 + TWO.means**2
        x = long_run.returns**2 - (long_run.returns**2).mean()
        for h in (1, 5, 20):
            expected = (pi @ v**2 - (pi @ v) ** 2) * lam**h
            assert np.mean(x[:-h] * x[h:]) == pytest.approx(expected, rel=0.15)

    def test_forecasts_tend_to_the_stationary_distribution(self):
        q0 = np.array([0.1, 0.2, 0.7])
        forecast = regime_forecast(THREE, q0, 500)
        np.testing.assert_allclose(forecast.probabilities[0], q0 @ THREE.transition)
        np.testing.assert_allclose(forecast.probabilities[-1], THREE.stationary_distribution,
                                   atol=1e-10)
        # The predictive moments are the mixture's: check day 3 by sampling it.
        rng = np.random.default_rng(12)
        regimes = rng.choice(3, size=400_000, p=forecast.probabilities[2])
        draws = THREE.means[regimes] + THREE.sigmas[regimes] * rng.standard_normal(len(regimes))
        assert draws.var() == pytest.approx(forecast.variances[2], rel=0.02)
        assert draws.mean() == pytest.approx(forecast.means[2], abs=4 * draws.std() / 632)


class TestValidation:
    @pytest.mark.parametrize("kwargs", [
        {"means": [], "sigmas": [], "transition": np.zeros((0, 0))},
        {"means": [0.0, 0.0], "sigmas": [0.01], "transition": np.eye(2)},
        {"means": [0.0, 0.0], "sigmas": [0.01, 0.0], "transition": np.eye(2)},
        {"means": [0.0, math.nan], "sigmas": [0.01, 0.02], "transition": np.eye(2)},
        {"means": [0.0, 0.0], "sigmas": [0.01, 0.02], "transition": [[0.9, 0.2], [0.0, 1.0]]},
        {"means": [0.0, 0.0], "sigmas": [0.01, 0.02], "transition": [[1.1, -0.1], [0.0, 1.0]]},
    ])
    def test_invalid_models_rejected(self, kwargs):
        with pytest.raises(ValueError):
            GaussianHMM(**kwargs)

    @pytest.mark.parametrize("bad", [
        lambda: fit_hmm(np.zeros(50) + 0.01, 2),                        # too few days
        lambda: fit_hmm(np.r_[np.full(200, 0.01), np.nan], 2),          # not finite
        lambda: fit_hmm(np.full(200, 0.01), 2),                         # no variation
        lambda: fit_hmm(simulate_hmm(TWO, 200, seed=1).returns, 0),
        lambda: hmm_filter(TWO, np.zeros((3, 2))),
        lambda: simulate_hmm(TWO, 0),
        lambda: regime_forecast(TWO, [0.5, 0.6], 5),
        lambda: regime_forecast(TWO, [0.5, 0.5], 0),
        lambda: TWO.permuted([0, 0]),
        lambda: select_regimes(simulate_hmm(TWO, 200, seed=1).returns, ()),
    ])
    def test_invalid_requests_rejected(self, bad):
        with pytest.raises(ValueError):
            bad()

    def test_models_are_immutable_copies(self):
        means = np.array([0.0, 0.001])
        model = GaussianHMM(means, [0.01, 0.02], np.eye(2))
        means[0] = 1.0
        assert model.means[0] == 0.0
        with pytest.raises(ValueError):
            model.means[0] = 2.0
