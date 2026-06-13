"""ZmqBus delivers messages across two sockets (the cross-process path, in one test)."""

from __future__ import annotations

import time

from agent.bus import ZmqBus
from contracts.messages import RadarDetection
from contracts.topics import Topics

ADDR = "tcp://127.0.0.1:5599"


def _detection() -> RadarDetection:
    return RadarDetection(radar_id="radar1", position=(100.0, 200.0, 50.0), timestamp=1.0)


def test_zmqbus_delivers_across_sockets() -> None:
    listener = ZmqBus(ADDR, bind=True)  # the "ground station" binds
    received: list[RadarDetection] = []
    listener.subscribe(Topics.RADAR_DETECTIONS, RadarDetection, received.append)

    radar = ZmqBus(ADDR, bind=False)  # the radar connects + publishes

    # ZeroMQ PUB/SUB drops messages sent before the subscription propagates;
    # publish repeatedly until one lands. On localhost the slow-joiner window is
    # tens of ms; 500 ms is a generous cap that keeps the test fast in CI.
    deadline = time.time() + 0.5
    while not received and time.time() < deadline:
        radar.publish(Topics.RADAR_DETECTIONS, _detection())
        listener.spin(timeout_ms=50)
        time.sleep(0.02)

    try:
        assert received, "no message delivered over ZeroMQ"
        assert received[0].radar_id == "radar1"
        assert received[0].timestamp == 1.0
    finally:
        radar.close()
        listener.close()
