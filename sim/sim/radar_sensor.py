"""
Radar sensor over externally-supplied ground truth.

When the *world* owns the truth (Gazebo publishing ``/simulation/ground_truth``),
the radar is just a sensor: it takes each object's true pose, runs it through a
Stone-Soup measurement model (Gaussian position noise + detection probability),
and publishes ``RadarDetection``. Wire ``observe`` as the handler for the
``GroundTruth`` topic.

This is the Gazebo-fed counterpart to ``StoneSoupRadar`` (which generates its own
synthetic truth for headless work). The downstream pipeline is identical — only
the *source* of truth differs.
"""

from __future__ import annotations

import numpy as np
from contracts.bus import Bus
from contracts.messages import GroundTruth, RadarDetection
from contracts.topics import Topics
from stonesoup.models.measurement.linear import LinearGaussian
from stonesoup.types.array import StateVector
from stonesoup.types.state import State

THREAT_KIND = "shahed"  # only threats are detected; interceptors are skipped


class RadarSensor:
    """Applies a measurement model to ground-truth poses and publishes detections."""

    def __init__(
        self,
        bus: Bus,
        radar_id: str,
        *,
        position_noise_m: float = 5.0,
        prob_detect: float = 1.0,
        seed: int = 0,
    ) -> None:
        np.random.seed(seed)  # Stone-Soup noise draws from numpy's global RNG
        self._rng = np.random.default_rng(seed)
        self._bus = bus
        self._radar_id = radar_id
        self._prob_detect = prob_detect
        self._measurement = LinearGaussian(
            ndim_state=6,
            mapping=(0, 2, 4),
            noise_covar=np.diag([position_noise_m**2] * 3),
        )

    def observe(self, ground_truth: GroundTruth) -> list[RadarDetection]:
        """Detect + publish for one ground-truth frame. Returns the hits."""
        detections: list[RadarDetection] = []
        for obj in ground_truth.objects:
            if obj.kind != THREAT_KIND or not obj.alive:
                continue
            if self._rng.random() >= self._prob_detect:
                continue
            px, py, pz = obj.position
            vx, vy, vz = obj.velocity
            state = State(StateVector([[px], [vx], [py], [vy], [pz], [vz]]))
            measured = self._measurement.function(state, noise=True)
            detection = RadarDetection(
                radar_id=self._radar_id,
                position=(float(measured[0, 0]), float(measured[1, 0]), float(measured[2, 0])),
                timestamp=ground_truth.timestamp,
            )
            self._bus.publish(Topics.RADAR_DETECTIONS, detection)
            detections.append(detection)
        return detections
