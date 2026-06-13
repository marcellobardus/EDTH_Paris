"""
Stone-Soup radar simulator.

Models incoming targets with Stone-Soup transition models, observes them
through a noisy Stone-Soup measurement model, and publishes every hit as a
``RadarDetection`` on ``Topics.RADAR_DETECTIONS``. Each ``scan(now)`` advances
the hidden ground truth one step and emits a detection per detected target —
"each time the radar sees something, it publishes."

The radar owns the simulated world (the target ground truth) on purpose: it is
a *simulator* standing in for a real sensor. Downstream (the ground station)
only ever sees the noisy ``RadarDetection`` stream.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from contracts.bus import Bus
from contracts.messages import RadarDetection
from contracts.topics import Topics
from stonesoup.models.measurement.linear import LinearGaussian
from stonesoup.models.transition.linear import (
    CombinedLinearGaussianTransitionModel,
    ConstantVelocity,
)
from stonesoup.types.array import StateVector
from stonesoup.types.detection import Detection
from stonesoup.types.groundtruth import GroundTruthState


@dataclass(frozen=True)
class TargetInit:
    """Initial 6-D state of a simulated target: [x, vx, y, vy, z, vz] (m, m/s)."""

    target_id: str
    state: tuple[float, float, float, float, float, float]


class StoneSoupRadar:
    """A radar that generates and publishes noisy detections of moving targets."""

    def __init__(
        self,
        bus: Bus,
        radar_id: str,
        targets: Iterable[TargetInit],
        *,
        start_time: datetime,
        position_noise_m: float = 5.0,
        process_noise: float = 1.0,
        prob_detect: float = 1.0,
        cull_range_m: float | None = None,
        seed: int = 0,
    ) -> None:
        self._rng = np.random.default_rng(seed)
        np.random.seed(seed)  # Stone-Soup noise draws from numpy's global RNG
        self._bus = bus
        self._radar_id = radar_id
        self._t0 = start_time
        self._time = start_time
        self._prob_detect = prob_detect
        self._cull_range_m = cull_range_m

        # 3 independent constant-velocity axes -> 6-D state [x,vx,y,vy,z,vz].
        self._transition = CombinedLinearGaussianTransitionModel(
            [ConstantVelocity(process_noise)] * 3
        )
        # Radar observes position (x, y, z) only, with Gaussian noise.
        self._measurement = LinearGaussian(
            ndim_state=6,
            mapping=(0, 2, 4),
            noise_covar=np.diag([position_noise_m**2] * 3),
        )
        self._truth: dict[str, GroundTruthState] = {
            t.target_id: GroundTruthState(
                StateVector(np.array(t.state, dtype=float).reshape(-1, 1)),
                timestamp=start_time,
            )
            for t in targets
        }

    def add_target(self, target: TargetInit) -> None:
        """Introduce a new target at the radar's current time (drones appearing over time)."""
        self._truth[target.target_id] = GroundTruthState(
            StateVector(np.array(target.state, dtype=float).reshape(-1, 1)),
            timestamp=self._time,
        )

    def scan(self, now: datetime) -> list[RadarDetection]:
        """Advance ground truth to ``now``, then detect + publish. Returns the hits."""
        interval: timedelta = now - self._time
        detections: list[RadarDetection] = []

        for target_id, truth in list(self._truth.items()):
            moved = GroundTruthState(
                self._transition.function(truth, noise=True, time_interval=interval),
                timestamp=now,
            )
            self._truth[target_id] = moved

            if self._cull_range_m is not None:
                px, py, pz = (float(moved.state_vector[i, 0]) for i in (0, 2, 4))
                if (px * px + py * py + pz * pz) ** 0.5 > self._cull_range_m:
                    del self._truth[target_id]
                    continue  # flew out of range

            if self._rng.random() >= self._prob_detect:
                continue  # missed this scan

            measured = Detection(
                self._measurement.function(moved, noise=True),
                timestamp=now,
                measurement_model=self._measurement,
            )
            x, y, z = (float(v) for v in measured.state_vector[:3, 0])
            detection = RadarDetection(
                radar_id=self._radar_id,
                position=(x, y, z),
                timestamp=(now - self._t0).total_seconds(),
            )
            self._bus.publish(Topics.RADAR_DETECTIONS, detection)
            detections.append(detection)

        self._time = now
        return detections
