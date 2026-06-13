"""
Continuously-running mock-world node.

The *world*: it owns the drones' true positions. New incoming drones appear every
random ``--min``–``--max`` seconds from a random bearing/range heading for the
origin, and every ``--step-interval`` (default 1 s) the world advances them and
publishes their true poses as a ``GroundTruth`` frame on
``Topics.GROUND_TRUTH``. No sensor noise here — the world is ground truth; the
radar is what corrupts it (see ``radar_sensor_node``).

This is the throwaway stand-in for Jules' Gazebo simulator: when Gazebo is ready
it publishes the same ``/simulation/ground_truth`` contract and this node is
simply not started — ``radar_sensor_node`` and the ground station are unchanged.

    # one terminal each, downstream first:
    uv run python -m gs.gs_node                              # ground station
    uv run python -m sim.radar_sensor_node                  # the radar
    uv run python -m sim.world_node --transport zmq          # the drones
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import time
from datetime import datetime

from contracts.bus import Bus
from contracts.topics import Topics

from sim.mock_ground_truth import MockGroundTruth

log = logging.getLogger("world")

Vec3 = tuple[float, float, float]


def random_drone(rng: random.Random) -> tuple[Vec3, Vec3]:
    """A drone 1–4 km out on a random bearing, heading for the origin."""
    bearing = rng.uniform(0.0, 2.0 * math.pi)
    distance = rng.uniform(1000.0, 4000.0)
    speed = rng.uniform(30.0, 60.0)
    altitude = rng.uniform(80.0, 250.0)
    position = (distance * math.cos(bearing), distance * math.sin(bearing), altitude)
    velocity = (-speed * math.cos(bearing), -speed * math.sin(bearing), 0.0)
    return position, velocity


def _make_bus(transport: str, addr: str) -> Bus:
    if transport == "zmq":
        from contracts.bus import ZmqBus

        return ZmqBus(addr, bind=False)  # the world connects; the radar sensor binds
    from contracts.bus import MockBroker

    return MockBroker().endpoint("world")  # in-process; nobody else hears it


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous mock-world (ground-truth) node")
    parser.add_argument(
        "--step-interval", type=float, default=1.0, help="seconds between world updates"
    )
    parser.add_argument("--min", type=float, default=5.0, help="min spawn interval seconds")
    parser.add_argument("--max", type=float, default=20.0, help="max spawn interval seconds")
    parser.add_argument("--ticks", type=int, default=0, help="stop after N steps (0 = forever)")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed (default: random)")
    parser.add_argument("--transport", choices=("mock", "zmq"), default="mock")
    parser.add_argument("--addr", default="tcp://127.0.0.1:5555", help="ground-truth address")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    rng = random.Random(args.seed)
    world = MockGroundTruth(_make_bus(args.transport, args.addr), start_time=datetime.now())

    log.info(
        "world (%s) stepping every %gs, new drone every %g-%gs, publishing on %s. Ctrl-C to stop.",
        args.transport,
        args.step_interval,
        args.min,
        args.max,
        Topics.GROUND_TRUTH,
    )
    steps = 0
    spawned = 0
    next_spawn = 0.0  # first drone appears on the first step, then at random intervals
    elapsed = 0.0
    try:
        while args.ticks == 0 or steps < args.ticks:
            time.sleep(args.step_interval)
            elapsed += args.step_interval
            if elapsed >= next_spawn:
                spawned += 1
                pos, vel = random_drone(rng)
                world.add(f"shahed-{spawned}", pos, vel)
                next_spawn = elapsed + rng.uniform(args.min, args.max)
                log.info("spawn shahed-%d", spawned)
            steps += 1
            frame = world.step(datetime.now())
            log.info(
                "step #%d t=%6.1fs  %d drone(s) airborne on %s",
                steps,
                frame.timestamp,
                len(frame.objects),
                Topics.GROUND_TRUTH,
            )
    except KeyboardInterrupt:
        log.info("world stopped.")


if __name__ == "__main__":
    main()
