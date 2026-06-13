"""Unit tests for the constant-velocity Kalman predictor (Phase 1)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from gs.predictor import ConstantVelocityPredictor
from stonesoup.types.prediction import GaussianStatePrediction
from stonesoup.types.state import GaussianState

T0 = datetime(2026, 6, 13, 12, 0, 0)


def _prior(state: list[float], var: float = 1e-6) -> GaussianState:
    """A near-certain Gaussian state at T0, ordered [x, vx, y, vy, z, vz]."""
    return GaussianState(
        np.array(state, dtype=float).reshape(-1, 1),
        np.eye(6) * var,
        timestamp=T0,
    )


def test_straight_line_propagation() -> None:
    # Moving +10 m/s along x, stationary in y, parked at z=100.
    prior = _prior([0, 10, 0, 0, 100, 0])
    pred = ConstantVelocityPredictor().predict(prior, T0 + timedelta(seconds=5))

    assert isinstance(pred, GaussianStatePrediction)
    x, vx, y, vy, z, vz = pred.state_vector.ravel()
    assert x == pytest.approx(50.0)  # 10 m/s * 5 s
    assert y == pytest.approx(0.0)
    assert z == pytest.approx(100.0)
    # Velocities are unchanged under constant velocity.
    assert (vx, vy, vz) == pytest.approx((10.0, 0.0, 0.0))


def test_covariance_grows_monotonically_with_dt() -> None:
    prior = _prior([0, 10, 0, 0, 100, 0])
    predictor = ConstantVelocityPredictor()

    traces = [
        np.trace(predictor.predict(prior, T0 + timedelta(seconds=dt)).covar) for dt in (1, 2, 5, 10)
    ]
    assert traces[0] > np.trace(prior.covar)  # grows vs prior
    assert all(b > a for a, b in zip(traces, traces[1:]))  # and monotonically


def test_zero_dt_is_identity() -> None:
    prior = _prior([0, 10, 0, 0, 100, 0])
    pred = ConstantVelocityPredictor().predict(prior, T0)

    assert pred.state_vector.ravel() == pytest.approx(prior.state_vector.ravel())
    assert np.allclose(pred.covar, prior.covar)


def test_backwards_time_raises() -> None:
    prior = _prior([0, 10, 0, 0, 100, 0])
    with pytest.raises(ValueError, match="precedes prior"):
        ConstantVelocityPredictor().predict(prior, T0 - timedelta(seconds=5))


def test_transition_model_is_shared() -> None:
    # The model exposed for the updater/initiator must be the exact object the
    # predictor uses internally, so the filter stays internally consistent.
    predictor = ConstantVelocityPredictor()
    prior = _prior([0, 10, 0, 0, 100, 0])
    pred = predictor.predict(prior, T0 + timedelta(seconds=3))
    assert pred.transition_model is predictor.transition_model


def test_larger_process_noise_inflates_covariance() -> None:
    # Higher q => the filter trusts the model less => more predicted uncertainty.
    prior = _prior([0, 10, 0, 0, 100, 0])
    target = T0 + timedelta(seconds=5)
    low = np.trace(ConstantVelocityPredictor(0.5).predict(prior, target).covar)
    high = np.trace(ConstantVelocityPredictor(5.0).predict(prior, target).covar)
    assert high > low
