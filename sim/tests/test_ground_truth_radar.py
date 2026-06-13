"""Gazebo-fed path: ground truth -> RadarSensor -> detections -> GS launch decision."""

from __future__ import annotations

from datetime import datetime, timedelta

from agent.bus import MockBroker
from contracts.messages import GroundTruth, RadarDetection
from contracts.topics import Topics
from gs.launch_decider import LaunchDecider
from sim.mock_ground_truth import MockGroundTruth
from sim.radar_sensor import RadarSensor

T0 = datetime(2026, 6, 13, 12, 0, 0)


def test_ground_truth_is_detected_and_published() -> None:
    broker = MockBroker()
    received: list[RadarDetection] = []
    broker.endpoint("gs").subscribe(Topics.RADAR_DETECTIONS, RadarDetection, received.append)

    world = MockGroundTruth(broker.endpoint("world"), start_time=T0)
    world.add("shahed-1", (2000.0, 0.0, 150.0), (-50.0, 0.0, 0.0))

    sensor = RadarSensor(broker.endpoint("radar1"), "radar1", seed=1)
    broker.endpoint("radar1").subscribe(Topics.GROUND_TRUTH, GroundTruth, sensor.observe)

    world.step(T0 + timedelta(seconds=1))

    assert len(received) == 1
    assert received[0].radar_id == "radar1"
    assert received[0].position[0] > 1900   # near the true 1950, with noise


def test_full_gazebo_fed_pipeline_drives_launches() -> None:
    broker = MockBroker()
    world = MockGroundTruth(broker.endpoint("world"), start_time=T0)
    world.add("shahed-1", (2000.0, 0.0, 150.0), (-50.0, 0.0, 0.0))
    world.add("shahed-2", (0.0, 2000.0, 150.0), (0.0, -50.0, 0.0))

    sensor = RadarSensor(broker.endpoint("radar1"), "radar1", seed=1)
    broker.endpoint("radar1").subscribe(Topics.GROUND_TRUTH, GroundTruth, sensor.observe)

    decider = LaunchDecider(broker.endpoint("gs"), interceptor_pool=3)

    for k in range(1, 6):
        world.step(T0 + timedelta(seconds=k))

    assert decider.threats_seen == 2
    assert decider.interceptors_committed == 2
