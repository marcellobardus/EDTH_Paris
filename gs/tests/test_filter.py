"""Unit tests for the single-target Kalman filter (Phase 2)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from contracts.messages import RadarDetection
from gs.filter import SingleTargetFilter

T0 = datetime(2026, 6, 13, 12, 0, 0)


def _det(pos: tuple[float, float, float], t: float) -> RadarDetection:
    return RadarDetection(radar_id="r1", position=pos, timestamp=t)


def test_initiate_seeds_position_with_zero_velocity() -> None:
    f = SingleTargetFilter(measurement_noise_m=5.0, initial_velocity_std_mps=50.0)
    state = f.initiate(_det((100.0, -20.0, 300.0), 0.0), T0)

    x, vx, y, vy, z, vz = state.state_vector.ravel()
    assert (x, y, z) == pytest.approx((100.0, -20.0, 300.0))
    assert (vx, vy, vz) == pytest.approx((0.0, 0.0, 0.0))
    # Position variance ~ R (25), velocity prior wide (50^2 = 2500).
    assert np.allclose(np.diag(state.covar), [25.0, 2500.0] * 3)


def test_one_update_pulls_toward_measurement_and_infers_velocity() -> None:
    f = SingleTargetFilter(measurement_noise_m=5.0)
    prior = f.initiate(_det((0.0, 0.0, 0.0), 0.0), T0)
    # True target moved +10 m/s in x over 1 s; measured slightly noisily.
    post = f.update(prior, _det((10.4, 0.0, 0.0), 1.0), T0 + timedelta(seconds=1))

    x, vx, _, _, _, _ = post.state_vector.ravel()
    assert 5.0 < x < 10.4  # pulled toward the measurement, not all the way
    assert vx > 3.0  # some positive x-velocity inferred
    assert np.trace(post.covar) < np.trace(prior.covar)  # measurement reduced uncertainty


def test_filter_beats_raw_measurement_noise_on_cv_trajectory() -> None:
    """Headline acceptance test: filtered position RMSE must beat the raw
    detection noise on a constant-velocity track."""
    rng = np.random.default_rng(42)
    noise_m = 5.0
    vel = np.array([50.0, 30.0, 0.0])  # m/s, constant
    dt = 1.0
    n = 30

    f = SingleTargetFilter(process_noise=1.0, measurement_noise_m=noise_m)
    truths: list[np.ndarray] = []
    raws: list[np.ndarray] = []
    filtered: list[np.ndarray] = []

    state = None
    for k in range(n):
        truth = vel * (k * dt)
        meas = truth + rng.normal(0.0, noise_m, size=3)
        truths.append(truth)
        raws.append(meas)

        det = _det((meas[0], meas[1], meas[2]), k * dt)
        ts = T0 + timedelta(seconds=k * dt)
        if state is None:
            state = f.initiate(det, ts)
        else:
            state = f.update(state, det, ts)
        filtered.append(np.array([state.state_vector[i, 0] for i in (0, 2, 4)]))

    # Compare over the converged tail (skip the warm-up).
    tail = slice(10, n)
    truth_arr = np.array(truths)[tail]
    raw_rmse = np.sqrt(np.mean(np.sum((np.array(raws)[tail] - truth_arr) ** 2, axis=1)))
    filt_rmse = np.sqrt(np.mean(np.sum((np.array(filtered)[tail] - truth_arr) ** 2, axis=1)))

    assert filt_rmse < raw_rmse, f"filter {filt_rmse:.2f} should beat raw {raw_rmse:.2f}"


def test_velocity_estimate_converges_to_truth() -> None:
    rng = np.random.default_rng(7)
    vel = np.array([40.0, -15.0, 0.0])
    f = SingleTargetFilter(process_noise=1.0, measurement_noise_m=5.0)

    state = None
    for k in range(40):
        truth = vel * k
        meas = truth + rng.normal(0.0, 5.0, size=3)
        det = _det((meas[0], meas[1], meas[2]), float(k))
        ts = T0 + timedelta(seconds=k)
        state = f.initiate(det, ts) if state is None else f.update(state, det, ts)

    est_vel = np.array([state.state_vector[i, 0] for i in (1, 3, 5)])
    assert np.allclose(est_vel, vel, atol=4.0)


def test_update_rejects_out_of_order_detection() -> None:
    f = SingleTargetFilter()
    prior = f.initiate(_det((0.0, 0.0, 0.0), 5.0), T0 + timedelta(seconds=5))
    with pytest.raises(ValueError, match="precedes prior"):
        f.update(prior, _det((0.0, 0.0, 0.0), 1.0), T0 + timedelta(seconds=1))


def test_shares_predictor_transition_model_with_updater_pathway() -> None:
    # Phase-3 hypothesiser needs the same predictor + updater objects.
    f = SingleTargetFilter()
    assert f.updater.measurement_model is f.measurement_model
    assert f.predictor.transition_model is f.predictor.transition_model
