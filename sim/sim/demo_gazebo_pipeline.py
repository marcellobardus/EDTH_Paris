"""
Preview of the integrated pipeline, before Gazebo is wired in.

A MockGroundTruth world spawns drones that fly toward the asset and publishes
their true poses on ``/simulation/ground_truth``; a RadarSensor turns those into
noisy detections on ``/radar/detections``; the LaunchDecider decides launch/hold.
When Gazebo is ready, the ONLY change is replacing MockGroundTruth with the real
``/simulation/ground_truth`` over ROS2 — RadarSensor and the GS stay identical.

    uv run python -m sim.demo_gazebo_pipeline --min 0.3 --max 0.6 --ticks 8 --seed 1
"""

from __future__ import annotations

import argparse
import math
import random
import time
from datetime import datetime

from contracts.bus import MockBroker
from contracts.messages import GroundTruth, RadarDetection
from contracts.topics import Topics
from gs.launch_decider import LaunchDecider, LaunchDecision

from sim.mock_ground_truth import MockGroundTruth
from sim.radar_sensor import RadarSensor

Vec3 = tuple[float, float, float]


def random_drone(rng: random.Random) -> tuple[Vec3, Vec3]:
    bearing = rng.uniform(0.0, 2.0 * math.pi)
    distance = rng.uniform(1500.0, 4000.0)
    speed = rng.uniform(30.0, 60.0)
    altitude = rng.uniform(80.0, 250.0)
    position = (distance * math.cos(bearing), distance * math.sin(bearing), altitude)
    velocity = (-speed * math.cos(bearing), -speed * math.sin(bearing), 0.0)
    return position, velocity


def main() -> None:
    parser = argparse.ArgumentParser(description="Gazebo-fed pipeline preview (mock world)")
    parser.add_argument("--min", type=float, default=5.0)
    parser.add_argument("--max", type=float, default=20.0)
    parser.add_argument("--ticks", type=int, default=0, help="stop after N steps (0 = forever)")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    broker = MockBroker()

    def on_detection(det: RadarDetection) -> None:
        x, y, z = det.position
        print(f"  radar  t={det.timestamp:6.1f}s  range={math.sqrt(x * x + y * y + z * z):6.0f} m")

    def on_decision(d: LaunchDecision) -> None:
        verb = f"LAUNCH {d.interceptor_id}" if d.launched else "HOLD"
        print(f"    >> {d.threat_id}: {verb}")

    # world --ground_truth--> radar --detections--> GS
    world = MockGroundTruth(broker.endpoint("world"), start_time=datetime.now())
    radar_bus = broker.endpoint("radar1")
    sensor = RadarSensor(radar_bus, "radar1", prob_detect=0.9, seed=rng.randrange(10**6))
    broker.endpoint("radar1").subscribe(Topics.GROUND_TRUTH, GroundTruth, sensor.observe)
    broker.endpoint("printer").subscribe(Topics.RADAR_DETECTIONS, RadarDetection, on_detection)
    LaunchDecider(broker.endpoint("gs"), interceptor_pool=3, on_decision=on_decision)

    print("World -> radar -> ground station (mock world; swap for Gazebo later). Ctrl-C to stop.\n")
    n = 0
    try:
        while args.ticks == 0 or n < args.ticks:
            time.sleep(rng.uniform(args.min, args.max))
            n += 1
            pos, vel = random_drone(rng)
            world.add(f"shahed-{n}", pos, vel)  # a new drone appears in the world
            world.step(datetime.now())  # advance + publish truth -> radar -> GS
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
