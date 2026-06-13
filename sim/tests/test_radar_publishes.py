"""The radar publishes a RadarDetection on Topics.RADAR_DETECTIONS for an incoming drone."""

from __future__ import annotations

from datetime import datetime, timedelta

from agent.bus import MockBroker
from contracts.messages import RadarDetection
from contracts.topics import Topics
from sim.radar_stonesoup import StoneSoupRadar, TargetInit


def test_incoming_drone_is_published_on_radar_topic() -> None:
    t0 = datetime(2026, 6, 13, 12, 0, 0)
    broker = MockBroker()

    received: list[RadarDetection] = []
    broker.endpoint("gs").subscribe(Topics.RADAR_DETECTIONS, RadarDetection, received.append)

    # One drone 3 km downrange on the x-axis, closing at ~45 m/s.
    drone = TargetInit("shahed-1", (3000.0, -45.0, 0.0, 0.0, 120.0, 0.0))
    radar = StoneSoupRadar(broker.endpoint("radar1"), "radar1", [drone], start_time=t0, seed=7)

    radar.scan(t0 + timedelta(seconds=1))

    assert len(received) == 1                 # exactly one hit published on the topic
    det = received[0]
    assert det.radar_id == "radar1"
    assert det.timestamp == 1.0
    assert det.position[0] > 2500             # still well downrange (3 km, with noise)


def test_drone_closes_in_over_successive_scans() -> None:
    t0 = datetime(2026, 6, 13, 12, 0, 0)
    broker = MockBroker()
    received: list[RadarDetection] = []
    broker.endpoint("gs").subscribe(Topics.RADAR_DETECTIONS, RadarDetection, received.append)

    drone = TargetInit("shahed-1", (3000.0, -45.0, 0.0, 0.0, 120.0, 0.0))
    radar = StoneSoupRadar(broker.endpoint("radar1"), "radar1", [drone], start_time=t0, seed=7)
    for k in range(1, 6):
        radar.scan(t0 + timedelta(seconds=k))

    assert len(received) == 5
    # x-position decreases scan over scan: the drone is coming.
    xs = [d.position[0] for d in received]
    assert xs[-1] < xs[0]
