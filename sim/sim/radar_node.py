"""
Continuously-running radar node.

Every random interval (default 5–20 s) a new incoming drone appears from a
random bearing/range and the radar scans + publishes detections to
``Topics.RADAR_DETECTIONS``. A printer subscribes to that topic so you can
watch the live stream; the node runs until Ctrl-C.

    uv run python -m sim.radar_node                 # 5–20 s intervals, forever
    uv run python -m sim.radar_node --min 0.3 --max 0.6 --ticks 5 --seed 1   # quick demo
"""

from __future__ import annotations

import argparse
import math
import random
import time
from datetime import datetime

from agent.bus import MockBroker
from contracts.messages import RadarDetection
from contracts.topics import Topics

from sim.radar_stonesoup import StoneSoupRadar, TargetInit


def random_incoming(target_id: str, rng: random.Random) -> TargetInit:
    """A drone 1–4 km out on a random bearing, heading for the origin."""
    bearing = rng.uniform(0.0, 2.0 * math.pi)
    distance = rng.uniform(1000.0, 4000.0)
    speed = rng.uniform(30.0, 60.0)
    x, y = distance * math.cos(bearing), distance * math.sin(bearing)
    vx, vy = -speed * math.cos(bearing), -speed * math.sin(bearing)  # toward origin
    altitude = rng.uniform(80.0, 250.0)
    return TargetInit(target_id, (x, vx, y, vy, altitude, 0.0))


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous Stone-Soup radar node")
    parser.add_argument("--min", type=float, default=5.0, help="min interval seconds")
    parser.add_argument("--max", type=float, default=20.0, help="max interval seconds")
    parser.add_argument("--ticks", type=int, default=0, help="stop after N scans (0 = forever)")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed (default: random)")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    broker = MockBroker()

    def on_detection(det: RadarDetection) -> None:
        x, y, z = det.position
        rng_m = math.sqrt(x * x + y * y + z * z)
        print(
            f"[{Topics.RADAR_DETECTIONS}] t={det.timestamp:7.1f}s  "
            f"pos=({x:8.1f},{y:8.1f},{z:6.1f})  range={rng_m:6.0f} m"
        )

    broker.endpoint("printer").subscribe(Topics.RADAR_DETECTIONS, RadarDetection, on_detection)

    radar = StoneSoupRadar(
        broker.endpoint("radar1"),
        "radar1",
        [],                       # no targets yet — they appear over time
        start_time=datetime.now(),
        prob_detect=0.9,
        cull_range_m=6000.0,      # drop drones that fly out of range (bounded memory)
        seed=rng.randrange(1_000_000),
    )

    print(
        f"Radar node running — new drone every {args.min:g}–{args.max:g}s, "
        f"publishing on {Topics.RADAR_DETECTIONS}. Ctrl-C to stop.\n"
    )
    n = 0
    try:
        while args.ticks == 0 or n < args.ticks:
            time.sleep(rng.uniform(args.min, args.max))
            n += 1
            radar.add_target(random_incoming(f"drone-{n}", rng))
            radar.scan(datetime.now())
    except KeyboardInterrupt:
        print("\nRadar node stopped.")


if __name__ == "__main__":
    main()
