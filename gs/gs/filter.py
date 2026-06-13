"""
Single-target Kalman filter.

Pairs the constant-velocity :class:`~gs.predictor.ConstantVelocityPredictor`
with a Stone Soup ``KalmanUpdater`` and a position-only measurement model to
turn a stream of noisy ``RadarDetection`` hits for *one* target into a filtered
6-D ``[x, vx, y, vy, z, vz]`` state estimate.

The component is **stateless** across calls — it never holds a track. Callers
keep the latest :class:`GaussianState` and feed it back in: :meth:`initiate`
seeds a track from the first detection, then :meth:`update` runs predict ->
update for each subsequent matched detection. This mirrors the predictor's
design and lets the multi-target tracker (phase 3) own one filter shared across
all tracks, reusing its :attr:`predictor` / :attr:`updater` /
:attr:`measurement_model` directly in the Stone Soup hypothesiser.

The measurement model mirrors the radar (``sim/sim/radar_stonesoup.py``):
position-only observations (mapping ``(0, 2, 4)``) with isotropic Gaussian noise.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
from contracts.messages import RadarDetection
from stonesoup.models.measurement.linear import LinearGaussian
from stonesoup.types.array import StateVector
from stonesoup.types.detection import Detection
from stonesoup.types.hypothesis import SingleHypothesis
from stonesoup.types.state import GaussianState
from stonesoup.types.update import GaussianStateUpdate
from stonesoup.updater.kalman import KalmanUpdater

from gs.predictor import ConstantVelocityPredictor


class SingleTargetFilter:
    """Filters one target's noisy position detections into a 6-D state estimate.

    ``measurement_noise_m`` is the per-axis standard deviation of the position
    measurement (metres); it sets the filter's R and should match the radar's
    ``position_noise_m`` (5.0). ``initial_velocity_std_mps`` is the 1-sigma prior
    on velocity at track birth: a single detection says nothing about velocity,
    so the seed covariance is deliberately wide there and collapses after a few
    updates.
    """

    def __init__(
        self,
        *,
        process_noise: float = 1.0,
        measurement_noise_m: float = 5.0,
        initial_velocity_std_mps: float = 50.0,
    ) -> None:
        self._predictor = ConstantVelocityPredictor(process_noise)
        # Observe position (x, y, z) only, with isotropic Gaussian noise — the
        # same model the radar uses to corrupt the truth.
        self._measurement_model = LinearGaussian(
            ndim_state=6,
            mapping=(0, 2, 4),
            noise_covar=np.diag([measurement_noise_m**2] * 3),
        )
        self._updater = KalmanUpdater(self._measurement_model)
        self._pos_var = measurement_noise_m**2
        self._vel_var = initial_velocity_std_mps**2

    @property
    def predictor(self) -> ConstantVelocityPredictor:
        """Shared predictor — hand to the phase-3 hypothesiser."""
        return self._predictor

    @property
    def updater(self) -> KalmanUpdater:
        """Shared updater — hand to the phase-3 hypothesiser."""
        return self._updater

    @property
    def measurement_model(self) -> LinearGaussian:
        """The position-only measurement model carrying R."""
        return self._measurement_model

    def initiate(self, detection: RadarDetection, timestamp: datetime) -> GaussianState:
        """Seed a new track from a single detection: position at the measured
        point with velocity zero, position variance ~ R and a wide velocity
        prior (we have no velocity information yet)."""
        x, y, z = detection.position
        mean = StateVector([[x], [0.0], [y], [0.0], [z], [0.0]])
        covar = np.diag(
            [self._pos_var, self._vel_var] * 3  # [x, vx, y, vy, z, vz]
        )
        return GaussianState(mean, covar, timestamp=timestamp)

    def update(
        self, prior: GaussianState, detection: RadarDetection, timestamp: datetime
    ) -> GaussianStateUpdate:
        """Run predict -> update: project ``prior`` to ``timestamp``, then
        correct it with ``detection``. Returns the posterior estimate."""
        prediction = self._predictor.predict(prior, timestamp)
        hypothesis = SingleHypothesis(prediction, self._to_measurement(detection, timestamp))
        return self._updater.update(hypothesis)

    def _to_measurement(self, detection: RadarDetection, timestamp: datetime) -> Detection:
        """Wrap a ``RadarDetection`` position as a Stone Soup ``Detection``."""
        x, y, z = detection.position
        return Detection(
            StateVector([[x], [y], [z]]),
            timestamp=timestamp,
            measurement_model=self._measurement_model,
        )
