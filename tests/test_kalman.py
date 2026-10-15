"""Tests for the Kalman filter, RTS smoother and structural-model MLE.

They follow the module docstring's validation plan: brute-force Gaussian
conditioning as the reference, the closed-form steady state, calibration on
simulated data, recovery of known variances, missing data, and bad inputs.
"""

import math

import numpy as np
import pytest

from quant_hub.models.kalman import (
    MIN_OBSERVATIONS,
    ZERO_LOG_RATIO,
    LocalLevel,
    LocalLinearTrend,
    StateSpaceModel,
    _fast_log_likelihood,
    diffuse_prior,
    fit_local_level,
    fit_local_linear_trend,
    kalman_filter,
    rts_smoother,
    simulate_state_space,
)

# 99.9% quantiles of chi-squared with 2 and 3 degrees of freedom.
CHI2_999 = {2: 13.816, 3: 16.266}


def random_model(m: int, rng: np.random.Generator) -> StateSpaceModel:
    A = rng.normal(size=(m, m))
    return StateSpaceModel(
        transition=0.5 * np.eye(m) + 0.4 * rng.normal(size=(m, m)),
        observation=rng.normal(size=m),
        state_covariance=0.1 * A @ A.T,
        observation_variance=rng.uniform(0.1, 1.0),
    )


def brute_force(model, y, a0, P0):
    """Condition the joint Gaussian of (x_1..x_n, y_1..y_n) directly.

    x_t = F^t x_0 + sum_{s=1..t} F^(t-s) w_s, so the stacked states are a
    linear map of z = (x_0, w_1, ..., w_n), whose covariance is
    block-diag(P0, Q, ..., Q). Returns a function that conditions x_t and
    y_t on the observed y at any set of times.
    """
    n, m = len(y), model.state_dim
    F, H = model.transition, model.observation
    Q, R = model.state_covariance, model.observation_variance

    M = np.zeros((n * m, (n + 1) * m))
    for t in range(1, n + 1):
        for s in range(t + 1):
            M[(t - 1) * m:t * m, s * m:(s + 1) * m] = np.linalg.matrix_power(F, t - s)
    cov_z = np.zeros(((n + 1) * m, (n + 1) * m))
    cov_z[:m, :m] = P0
    for s in range(1, n + 1):
        cov_z[s * m:(s + 1) * m, s * m:(s + 1) * m] = Q
    mean_z = np.concatenate([a0, np.zeros(n * m)])

    mean_x = M @ mean_z
    cov_x = M @ cov_z @ M.T
    G = np.kron(np.eye(n), H[None, :])
    mean_y = G @ mean_x
    cov_y = G @ cov_x @ G.T + R * np.eye(n)
    cov_xy = cov_x @ G.T

    def condition(t, given):
        """Mean/cov of x_t and of y_t given the observed y at times ``given``."""
        rows = slice(t * m, (t + 1) * m)
        idx = [s for s in given if not np.isnan(y[s])]
        if not idx:
            return mean_x[rows], cov_x[rows, rows], mean_y[t], cov_y[t, t]
        Syy = cov_y[np.ix_(idx, idx)]
        resid = y[idx] - mean_y[idx]
        Bx = np.linalg.solve(Syy, cov_xy[rows][:, idx].T).T
        By = np.linalg.solve(Syy, cov_y[t, idx])
        return (
            mean_x[rows] + Bx @ resid,
            cov_x[rows, rows] - Bx @ cov_xy[rows][:, idx].T,
            mean_y[t] + By @ resid,
            cov_y[t, t] - By @ cov_y[t, idx],
        )

    observed = [s for s in range(n) if not np.isnan(y[s])]
    joint = cov_y[np.ix_(observed, observed)]
    resid = y[observed] - mean_y[observed]
    _, logdet = np.linalg.slogdet(joint)
    log_density = -0.5 * (
        len(observed) * math.log(2 * math.pi) + logdet + resid @ np.linalg.solve(joint, resid)
    )
    return condition, log_density


class TestAgainstBruteForce:
    @pytest.mark.parametrize("m", [1, 2])
    def test_filter_and_smoother_equal_gaussian_conditioning(self, m):
        rng = np.random.default_rng(10 + m)
        model = random_model(m, rng)
        y = rng.normal(size=9).cumsum()
        y[4] = np.nan  # a missing value leaves conditioning
        a0, P0 = rng.normal(size=m), np.eye(m) * 2.0
        result = kalman_filter(model, y, a0, P0)
        smoothed = rts_smoother(result)
        condition, log_density = brute_force(model, y, a0, P0)

        n = len(y)
        for t in range(n):
            pred_x, pred_P, pred_y, pred_S = condition(t, range(t))
            filt_x, filt_P, _, _ = condition(t, range(t + 1))
            smooth_x, smooth_P, _, _ = condition(t, range(n))
            np.testing.assert_allclose(result.predicted_means[t], pred_x, rtol=1e-10, atol=1e-10)
            np.testing.assert_allclose(result.predicted_covariances[t], pred_P, rtol=1e-10, atol=1e-10)
            np.testing.assert_allclose(result.filtered_means[t], filt_x, rtol=1e-10, atol=1e-10)
            np.testing.assert_allclose(result.filtered_covariances[t], filt_P, rtol=1e-10, atol=1e-10)
            np.testing.assert_allclose(smoothed.means[t], smooth_x, rtol=1e-10, atol=1e-10)
            np.testing.assert_allclose(smoothed.covariances[t], smooth_P, rtol=1e-10, atol=1e-10)
            assert result.innovation_variances[t] == pytest.approx(pred_S, rel=1e-10)
            if not np.isnan(y[t]):
                assert result.innovations[t] == pytest.approx(y[t] - pred_y, rel=1e-10, abs=1e-10)

        # Every observed term together is the joint density of the observed y.
        observed = ~np.isnan(y)
        e, S = result.innovations[observed], result.innovation_variances[observed]
        total = -0.5 * float(np.sum(math.log(2 * math.pi) + np.log(S) + e**2 / S))
        assert total == pytest.approx(log_density, rel=1e-10)

    def test_likelihood_skips_the_first_m_observed_terms(self):
        y = np.array([np.nan, 1.0, 1.4, np.nan, 0.9, 1.3, 1.1])
        result = kalman_filter(LocalLinearTrend(0.1, 0.01, 0.2).state_space(), y)
        assert result.likelihood_mask.tolist() == [False, False, False, False, True, True, True]
        e = result.innovations[result.likelihood_mask]
        S = result.innovation_variances[result.likelihood_mask]
        expected = -0.5 * float(np.sum(math.log(2 * math.pi) + np.log(S) + e**2 / S))
        assert result.log_likelihood == pytest.approx(expected)

    def test_missing_value_skips_the_update(self):
        y = np.array([1.0, 1.2, np.nan, 1.1, 0.8])
        result = kalman_filter(LocalLevel(0.3, 0.5).state_space(), y)
        assert np.isnan(result.innovations[2])
        assert np.all(result.gains[2] == 0)
        np.testing.assert_array_equal(result.filtered_means[2], result.predicted_means[2])
        np.testing.assert_array_equal(result.filtered_covariances[2], result.predicted_covariances[2])


class TestSteadyState:
    @pytest.mark.parametrize("q", [1e-4, 0.05, 0.5, 4.0, 300.0])
    def test_gain_formula_is_the_fixed_point(self, q):
        # Iterate p -> p/(1+p) + q from p = q to the fixed point.
        p = q
        for _ in range(200_000):
            p_next = p / (1 + p) + q
            if abs(p_next - p) < 1e-15 * p_next:
                break
            p = p_next
        assert LocalLevel(q, 1.0).steady_state_gain == pytest.approx(p / (1 + p), rel=1e-9)

    def test_gain_limits(self):
        assert LocalLevel(0.0, 1.0).steady_state_gain == 0.0
        assert LocalLevel(1e-12, 1.0).steady_state_gain == pytest.approx(1e-6, rel=1e-5)
        assert LocalLevel(1e12, 1.0).steady_state_gain == pytest.approx(1.0, abs=1e-11)

    def test_filter_gain_converges_from_a_diffuse_start(self):
        params = LocalLevel(level_variance=0.5, noise_variance=1.0)
        y = np.random.default_rng(1).normal(size=300).cumsum()
        result = kalman_filter(params.state_space(), y)
        assert result.gains[-1, 0] == pytest.approx(params.steady_state_gain, rel=1e-12)

    def test_converged_filter_is_the_ewma(self):
        # Starting at P_0 = K R makes P_{1|0} the fixed point, so every gain is
        # the steady-state K and the filter must be exactly an EWMA.
        params = LocalLevel(level_variance=0.5, noise_variance=1.0)
        gain = params.steady_state_gain
        y = np.random.default_rng(2).normal(size=200).cumsum()
        a0 = 0.3
        result = kalman_filter(params.state_space(), y, [a0], [[gain * params.noise_variance]])
        np.testing.assert_allclose(result.gains[:, 0], gain, rtol=1e-12)
        ewma = np.empty_like(y)
        level = a0
        for t, value in enumerate(y):
            level += gain * (value - level)
            ewma[t] = level
        np.testing.assert_allclose(result.filtered_means[:, 0], ewma, rtol=1e-12)


class TestCalibration:
    def test_true_parameters_give_calibrated_bands_and_white_innovations(self):
        params = LocalLevel(level_variance=1e-4, noise_variance=4e-5)
        sim = simulate_state_space(params.state_space(), 5000, initial_state=[4.6], seed=5)
        result = kalman_filter(params.state_space(), sim.observations)

        error = sim.states[:, 0] - result.filtered_means[:, 0]
        band = 1.96 * np.sqrt(result.filtered_covariances[:, 0, 0])
        # Coverage indicators are autocorrelated, so allow about 3x the
        # binomial standard error of 0.003.
        assert np.mean(np.abs(error) <= band) == pytest.approx(0.95, abs=0.012)

        z = result.standardized_innovations[result.likelihood_mask]
        n = len(z)
        assert abs(z.mean()) < 4 / math.sqrt(n)
        assert z.var() == pytest.approx(1.0, abs=4 * math.sqrt(2 / n))
        centered = z - z.mean()
        for lag in range(1, 6):
            rho = centered[lag:] @ centered[:-lag] / (centered @ centered)
            assert abs(rho) < 4 / math.sqrt(n)

    def test_smoothed_band_is_calibrated_and_tighter(self):
        params = LocalLevel(level_variance=1e-4, noise_variance=4e-5)
        sim = simulate_state_space(params.state_space(), 5000, initial_state=[4.6], seed=6)
        result = kalman_filter(params.state_space(), sim.observations)
        smoothed = rts_smoother(result)
        error = sim.states[:, 0] - smoothed.means[:, 0]
        band = 1.96 * np.sqrt(smoothed.covariances[:, 0, 0])
        assert np.mean(np.abs(error) <= band) == pytest.approx(0.95, abs=0.012)
        assert np.all(smoothed.covariances[:, 0, 0] <= result.filtered_covariances[:, 0, 0] * (1 + 1e-12))

    def test_likelihood_insensitive_to_kappa(self):
        params = LocalLinearTrend(1e-4, 1e-7, 2.5e-5)
        sim = simulate_state_space(params.state_space(), 1000, initial_state=[4.6, 0.0], seed=3)
        a0, P0 = diffuse_prior(sim.observations, 2)
        values = [
            kalman_filter(params.state_space(), sim.observations, a0, P0 * factor).log_likelihood
            for factor in (1.0, 100.0)
        ]
        assert values[0] == pytest.approx(values[1], abs=1e-3)


class TestFastLikelihood:
    @pytest.mark.parametrize("m", [1, 2, 3])
    def test_matches_the_reference_filter(self, m):
        rng = np.random.default_rng(40 + m)
        for _ in range(5):
            model = random_model(m, rng)
            y = rng.normal(size=80).cumsum()
            y[rng.choice(80, 8, replace=False)] = np.nan
            a0, P0 = diffuse_prior(y, m)
            reference = kalman_filter(model, y, a0, P0).log_likelihood
            assert _fast_log_likelihood(model, y, a0, P0) == pytest.approx(reference, rel=1e-12)


class TestSimulation:
    def test_seed_is_deterministic(self):
        model = LocalLinearTrend(1e-4, 1e-7, 2.5e-5).state_space()
        a = simulate_state_space(model, 50, seed=4)
        b = simulate_state_space(model, 50, seed=4)
        np.testing.assert_array_equal(a.states, b.states)
        np.testing.assert_array_equal(a.observations, b.observations)

    def test_singular_state_covariance_keeps_slope_fixed(self):
        model = LocalLinearTrend(1e-4, 0.0, 2.5e-5).state_space()
        sim = simulate_state_space(model, 100, initial_state=[4.6, 0.002], seed=1)
        np.testing.assert_allclose(sim.states[:, 1], 0.002, rtol=0, atol=1e-15)

    def test_noise_variances_match(self):
        params = LocalLevel(level_variance=1e-4, noise_variance=4e-5)
        sim = simulate_state_space(params.state_space(), 20_000, seed=8)
        level_steps = np.diff(sim.states[:, 0])
        noise = sim.observations - sim.states[:, 0]
        assert level_steps.var() == pytest.approx(1e-4, rel=0.05)
        assert noise.var() == pytest.approx(4e-5, rel=0.05)


class TestFit:
    def test_local_level_recovery(self):
        true = LocalLevel(level_variance=1e-4, noise_variance=2.5e-5)
        sim = simulate_state_space(true.state_space(), 5040, initial_state=[4.6], seed=7)
        fit = fit_local_level(sim.observations)
        got = fit.parameters
        assert fit.converged
        assert fit.zero_variances == ()
        # About 4 sampling standard deviations (measured over 30 seeds).
        assert got.level_variance == pytest.approx(true.level_variance, rel=0.12)
        assert got.noise_variance == pytest.approx(true.noise_variance, rel=0.35)
        # The maximum must beat the truth, and by no more than the
        # likelihood-ratio test allows.
        at_truth = kalman_filter(true.state_space(), sim.observations).log_likelihood
        assert fit.log_likelihood >= at_truth - 1e-6
        assert 2 * (fit.log_likelihood - at_truth) < CHI2_999[2]
        assert fit.log_likelihood == fit.filtered.log_likelihood

    def test_local_linear_trend_recovery(self):
        true = LocalLinearTrend(level_variance=1e-4, slope_variance=1e-7, noise_variance=2.5e-5)
        sim = simulate_state_space(true.state_space(), 2520, initial_state=[4.6, 0.0], seed=1)
        fit = fit_local_linear_trend(sim.observations)
        got = fit.parameters
        assert fit.converged
        assert got.level_variance == pytest.approx(true.level_variance, rel=0.16)
        assert got.noise_variance == pytest.approx(true.noise_variance, rel=0.25)
        # The slope variance is weakly identified (30% sampling spread).
        assert abs(math.log(got.slope_variance / true.slope_variance)) < 1.1
        at_truth = kalman_filter(true.state_space(), sim.observations).log_likelihood
        assert fit.log_likelihood >= at_truth - 1e-6
        assert 2 * (fit.log_likelihood - at_truth) < CHI2_999[3]

    def test_positively_autocorrelated_changes_put_noise_at_zero(self):
        # The model implies Corr(dy_t, dy_{t-1}) <= 0, so AR(1) changes with
        # phi = 0.2 can only be fit at noise variance zero.
        rng = np.random.default_rng(9)
        shocks = rng.normal(scale=0.01, size=1500)
        changes = np.empty_like(shocks)
        changes[0] = shocks[0]
        for t in range(1, len(shocks)):
            changes[t] = 0.2 * changes[t - 1] + shocks[t]
        y = 4.6 + np.cumsum(changes)
        fit = fit_local_level(y)
        assert fit.zero_variances == ("noise_variance",)
        scale = np.var(np.diff(y))
        assert fit.parameters.noise_variance < math.exp(ZERO_LOG_RATIO) * scale
        # With no noise the innovations are the daily changes, so the MLE of
        # the level variance is their mean square.
        assert fit.parameters.level_variance == pytest.approx(np.mean(np.diff(y) ** 2), rel=1e-4)

    @pytest.mark.parametrize("seed", range(8))
    def test_zero_flag_follows_the_sign_of_the_autocorrelation(self, seed):
        # Pure random walks: the sample decides which side of zero the
        # implied autocorrelation lands, and the fit must report it.
        y = 4.6 + np.cumsum(np.random.default_rng(seed).normal(scale=0.01, size=1500))
        changes = np.diff(y) - np.diff(y).mean()
        rho = changes[1:] @ changes[:-1] / (changes @ changes)
        fit = fit_local_level(y)
        if rho > 0.005:
            assert fit.zero_variances == ("noise_variance",)
        elif rho < -0.005:
            assert fit.zero_variances == ()

    def test_missing_values_are_tolerated(self):
        true = LocalLevel(level_variance=1e-4, noise_variance=2.5e-5)
        y = simulate_state_space(true.state_space(), 600, initial_state=[4.6], seed=2).observations
        y[[50, 51, 300]] = np.nan
        fit = fit_local_level(y)
        assert np.isfinite(fit.log_likelihood)
        assert np.isnan(fit.filtered.innovations[[50, 51, 300]]).all()
        assert fit.filtered.likelihood_mask.sum() == len(y) - 3 - 1


class TestValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"transition": np.ones((1, 2))},
            {"transition": np.full((1, 1), np.inf)},
            {"observation": np.ones(2)},
            {"state_covariance": np.array([[-1.0]])},
            {"state_covariance": np.ones((2, 2))},
            {"observation_variance": 0.0},
            {"observation_variance": math.nan},
        ],
    )
    def test_invalid_model_rejected(self, kwargs):
        base = {
            "transition": np.ones((1, 1)),
            "observation": np.ones(1),
            "state_covariance": np.ones((1, 1)),
            "observation_variance": 1.0,
        }
        base.update(kwargs)
        with pytest.raises(ValueError):
            StateSpaceModel(**base)

    def test_asymmetric_or_indefinite_covariance_rejected(self):
        with pytest.raises(ValueError):
            StateSpaceModel(np.eye(2), np.ones(2), np.array([[1.0, 0.5], [0.0, 1.0]]), 1.0)
        with pytest.raises(ValueError):
            StateSpaceModel(np.eye(2), np.ones(2), np.array([[1.0, 2.0], [2.0, 1.0]]), 1.0)

    @pytest.mark.parametrize(
        "args",
        [(-1e-4, 1e-5), (1e-4, 0.0), (math.nan, 1e-5), (1e-4, math.inf)],
    )
    def test_invalid_variances_rejected(self, args):
        with pytest.raises(ValueError):
            LocalLevel(*args)
        with pytest.raises(ValueError):
            LocalLinearTrend(args[0], 1e-7, args[1])

    @pytest.mark.parametrize(
        "observations",
        [[], [[1.0, 2.0]], [1.0, math.inf, 2.0], [math.nan, math.nan]],
    )
    def test_invalid_observations_rejected(self, observations):
        with pytest.raises(ValueError):
            kalman_filter(LocalLevel(1.0, 1.0).state_space(), np.array(observations))

    def test_invalid_priors_rejected(self):
        model = LocalLevel(1.0, 1.0).state_space()
        y = np.array([1.0, 2.0, 1.5])
        with pytest.raises(ValueError):
            kalman_filter(model, y, initial_mean=[0.0])
        with pytest.raises(ValueError):
            kalman_filter(model, y, [0.0, 0.0], [[1.0]])
        with pytest.raises(ValueError):
            kalman_filter(model, y, [0.0], [[-1.0]])
        with pytest.raises(ValueError):
            diffuse_prior(np.full(5, 3.0), 1)

    def test_invalid_fit_inputs_rejected(self):
        with pytest.raises(ValueError):
            fit_local_level(np.arange(MIN_OBSERVATIONS - 1, dtype=float) ** 2)
        with pytest.raises(ValueError):
            fit_local_level(np.full(MIN_OBSERVATIONS, 4.6))
        with pytest.raises(ValueError):
            fit_local_level(np.arange(MIN_OBSERVATIONS, dtype=float) ** 2, max_iterations=0)

    def test_invalid_simulation_rejected(self):
        model = LocalLevel(1.0, 1.0).state_space()
        with pytest.raises(ValueError):
            simulate_state_space(model, 0)
        with pytest.raises(ValueError):
            simulate_state_space(model, 10, initial_state=[0.0, 0.0])
