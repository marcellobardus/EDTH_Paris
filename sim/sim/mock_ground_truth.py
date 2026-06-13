"""
Mock ground-truth publisher — stands in for Gazebo until the real sim is ready.

Holds a few straight-line drones and, on each ``step(now)``, advances them and
publishes a ``GroundTruth`` message on ``Topics.GROUND_TRUTH`` — the exact
contract Gazebo will publish. Lets us build and test the Gazebo-fed radar path
now; swap this for the real ``/simulation/ground_truth`` (over ROS2) and the
``RadarSensor`` + ground station code is unchanged.
"""

from __future__ import annotations

from datetime import datetime

from contracts.bus import Bus
from contracts.messages import GroundTruth, GroundTruthObject
from contracts.topics import Topics

Vec3 = tuple[float, float, float]


class MockGroundTruth:
    """A toy world: constant-velocity drones, published as GroundTruth frames."""

    def __init__(self, bus: Bus, *, start_time: datetime) -> None:
        self._bus = bus
        self._t0 = start_time
        self._time = start_time
        self._objects: dict[str, list[list[float]]] = {}  # id -> [position, velocity]

    def add(self, object_id: str, position: Vec3, velocity: Vec3) -> None:
        self._objects[object_id] = [list(position), list(velocity)]

    def step(self, now: datetime) -> GroundTruth:
        dt = (now - self._time).total_seconds()
        objects: list[GroundTruthObject] = []
        for object_id, (pos, vel) in self._objects.items():
            for i in range(3):
                pos[i] += vel[i] * dt
            objects.append(
                GroundTruthObject(
                    object_id=object_id,
                    kind="shahed",
                    position=(pos[0], pos[1], pos[2]),
                    velocity=(vel[0], vel[1], vel[2]),
                    alive=True,
                )
            )
        self._time = now
        frame = GroundTruth(objects=objects, timestamp=(now - self._t0).total_seconds())
        self._bus.publish(Topics.GROUND_TRUTH, frame)
        return frame
