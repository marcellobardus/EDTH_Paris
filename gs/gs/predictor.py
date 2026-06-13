"""
Constant-velocity Kalman predictor.

The "predict" half of the ground-station filter: projects a track's Gaussian
state forward to a target time under a constant-velocity model. No measurements,
no association — those live in the updater / tracker. Isolated here so the
motion model is swappable in one place and the prediction step is unit-testable
on its own.

The transition model deliberately mirrors the radar simulator
(``sim/sim/radar_stonesoup.py``): a 6-D ``[x, vx, y, vy, z, vz]`` state built
from three independent ``ConstantVelocity`` axes. Sharing the model family with
the simulator keeps the filter well-matched to the data it consumes.
"""

from __future__ import annotations

from datetime import datetime

from stonesoup.models.transition.linear import (
    CombinedLinearGaussianTransitionModel,
    ConstantVelocity,
)
from stonesoup.predictor.kalman import KalmanPredictor
from stonesoup.types.prediction import GaussianStatePrediction
from stonesoup.types.state import GaussianState


class ConstantVelocityPredictor:
    """Projects a 6-D ``[x, vx, y, vy, z, vz]`` Gaussian track state forward in
    time under a constant-velocity model. Predict-only; pairs with a
    ``KalmanUpdater`` sharing the same :attr:`transition_model`.

    ``process_noise`` is the constant-velocity power-spectral-density ``q``
    (m^2/s^3) applied per axis — the single most important tuning knob. A larger
    ``q`` makes the filter trust the motion model less, so tracks adapt faster to
    manoeuvres at the cost of noisier estimates. Start at the radar's
    ``process_noise`` (1.0) and raise it if Shaheds manoeuvre and tracks lag.
    """

    def __init__(self, process_noise: float = 1.0) -> None:
        # 3 independent constant-velocity axes -> 6-D state [x,vx,y,vy,z,vz],
        # matching sim/sim/radar_stonesoup.py so filter and simulator agree.
        self._transition = CombinedLinearGaussianTransitionModel(
            [ConstantVelocity(process_noise)] * 3
        )
        self._predictor = KalmanPredictor(self._transition)

    @property
    def transition_model(self) -> CombinedLinearGaussianTransitionModel:
        """The shared transition model. Hand this same object to the paired
        updater / initiator so the filter is internally consistent."""
        return self._transition

    def predict(self, prior: GaussianState, timestamp: datetime) -> GaussianStatePrediction:
        """Project ``prior`` forward to ``timestamp``.

        The time step is ``timestamp - prior.timestamp``. The mean propagates as
        ``x' = F(dt) @ x`` (positions advance by ``v * dt``, velocities
        unchanged) and the covariance grows by ``F @ P @ F.T + Q(dt)``. A zero
        step returns the prior unchanged.

        Predicting to a time *before* the prior raises ``ValueError`` — Stone
        Soup would otherwise run the model backwards (and still *inflate* the
        covariance), which is never what a forward filter wants; smoothing is out
        of scope here.
        """
        if prior.timestamp is not None and timestamp < prior.timestamp:
            raise ValueError("predict() target time precedes prior state time")
        return self._predictor.predict(prior, timestamp=timestamp)
