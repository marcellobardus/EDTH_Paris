"""
Runnable demo: one incoming drone -> the radar publishes to /radar/detections.

Run it:
    uv run python -m sim.demo_radar

A listener subscribes to ``Topics.RADAR_DETECTIONS`` and prints every hit, so
you can watch the drone closing in and confirm we publish on that exact topic.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from agent.bus import MockBroker
from contracts.messages import RadarDetection
from contracts.topics import Topics

from sim.radar_stonesoup import StoneSoupRadar, TargetInit


def main() -> None:
    t0 = datetime(2026, 6, 13, 12, 0, 0)
    broker = MockBroker()

    received: list[RadarDetection] = []

    def on_radar(det: RadarDetection) -> None:
        received.append(det)
        x, y, z = det.position
        rng = math.sqrt(x * x + y * y + z * z)
        print(
            f"  [{Topics.RADAR_DETECTIONS}] t={det.timestamp:4.1f}s  "
            f"pos=({x:7.1f},{y:7.1f},{z:6.1f})  range={rng:6.0f} m"
        )

    # Subscribe to the radar topic — receiving here proves we published to it.
    broker.endpoint("listener").subscribe(Topics.RADAR_DETECTIONS, RadarDetection, on_radar)

    # One Shahed-class drone 3 km out on the x-axis, closing at ~45 m/s.
    drone = TargetInit("shahed-1", (3000.0, -45.0, 0.0, 0.0, 120.0, 0.0))
    radar = StoneSoupRadar(broker.endpoint("radar1"), "radar1", [drone], start_time=t0, seed=7)

    print(f"Incoming drone — radar publishing on {Topics.RADAR_DETECTIONS}\n")
    for k in range(1, 9):
        radar.scan(t0 + timedelta(seconds=k))

    print(f"\nPublished {len(received)} detections on {Topics.RADAR_DETECTIONS}.")


if __name__ == "__main__":
    main()
