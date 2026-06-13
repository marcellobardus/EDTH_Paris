"""Unit tests for the radar-sensor node's GroundTruth rehydration."""

from __future__ import annotations

import dataclasses
import json

from contracts.bus import MockBroker
from contracts.messages import GroundTruth, GroundTruthObject, RadarDetection
from contracts.topics import Topics
from sim.radar_sensor import RadarSensor
from sim.radar_sensor_node import _rehydrate


def _json_roundtrip(gt: GroundTruth) -> GroundTruth:
    """Mimic ZmqBus: asdict -> JSON -> msg_type(**data). Nested dataclasses come
    back as plain dicts, exactly as they arrive over the wire."""
    wire = json.dumps(dataclasses.asdict(gt))
    return GroundTruth(**json.loads(wire))


def _frame() -> GroundTruth:
    return GroundTruth(
        objects=[
            GroundTruthObject("shahed-1", "shahed", (2000.0, 0.0, 150.0), (-50.0, 0.0, 0.0), True),
            GroundTruthObject("shahed-2", "shahed", (0.0, 1500.0, 120.0), (0.0, -40.0, 0.0), True),
        ],
        timestamp=1.0,
    )


def test_roundtrip_flattens_objects_to_dicts() -> None:
    # Establishes the failure mode rehydrate exists to fix.
    flattened = _json_roundtrip(_frame())
    assert all(isinstance(obj, dict) for obj in flattened.objects)


def test_rehydrate_restores_groundtruth_objects() -> None:
    rehydrated = _rehydrate(_json_roundtrip(_frame()))
    assert all(isinstance(obj, GroundTruthObject) for obj in rehydrated.objects)
    assert [obj.kind for obj in rehydrated.objects] == ["shahed", "shahed"]
    assert rehydrated.objects[0].object_id == "shahed-1"
    assert rehydrated.timestamp == 1.0


def test_rehydrate_is_noop_on_native_objects() -> None:
    # In-process MockBroker delivers real dataclasses; rehydrate must pass through.
    frame = _frame()
    rehydrated = _rehydrate(frame)
    assert all(isinstance(obj, GroundTruthObject) for obj in rehydrated.objects)


def test_sensor_observes_rehydrated_frame() -> None:
    # The regression: a wire-decoded frame must drive the sensor without raising.
    broker = MockBroker()
    received: list[RadarDetection] = []
    broker.endpoint("gs").subscribe(Topics.RADAR_DETECTIONS, RadarDetection, received.append)
    sensor = RadarSensor(broker.endpoint("radar1"), "radar1", prob_detect=1.0, seed=1)

    sensor.observe(_rehydrate(_json_roundtrip(_frame())))

    assert len(received) == 2
    assert {d.radar_id for d in received} == {"radar1"}
