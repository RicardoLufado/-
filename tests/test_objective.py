"""ramp 目标函数 = P(W ≥ τ)，τ ~ U[low, high]。"""
import numpy as np
import pytest

from engine.portfolio import neg_variance_objective, ramp_objective

LOW, HIGH = 2_080_000.0, 2_160_000.0


def test_ramp_pointwise():
    W = np.array([[1_900_000.0], [2_080_000.0], [2_120_000.0], [2_160_000.0], [2_500_000.0]])
    vals = np.clip((W[:, 0] - LOW) / (HIGH - LOW), 0, 1)
    np.testing.assert_allclose(vals, [0, 0, 0.5, 1, 1])
    assert ramp_objective(W, LOW, HIGH)[0] == pytest.approx(np.mean([0, 0, 0.5, 1, 1]))


def test_ramp_equals_average_over_uniform_threshold():
    rng = np.random.default_rng(42)
    W = rng.normal(2_100_000, 60_000, size=(5000, 1))
    taus = np.linspace(LOW, HIGH, 4001)
    brute = np.mean([(W[:, 0] >= t).mean() for t in taus])
    assert ramp_objective(W, LOW, HIGH)[0] == pytest.approx(brute, abs=2e-3)


def test_ramp_vectorised_over_candidates():
    W = np.array([[2_000_000.0, 2_200_000.0], [2_120_000.0, 2_120_000.0]])
    np.testing.assert_allclose(ramp_objective(W, LOW, HIGH), [0.25, 0.75])


def test_neg_variance():
    W = np.array([[1.0, 0.0], [3.0, 0.0]])
    np.testing.assert_allclose(neg_variance_objective(W), [-1.0, 0.0])
