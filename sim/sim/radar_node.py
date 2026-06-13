"""
Continuously-running radar node.

The radar scans at a fixed cadence (``--scan-interval``, default 1 s), re-seeing
every live target each scan and publishing detections to
``Topics.RADAR_DETECTIONS`` — a steady revisit rate, like a real radar. New
incoming drones appear independently, every random ``--min``–``--max`` seconds,
from a random bearing/range heading for the origin. Runs until Ctrl-C.

The scan rate is deliberately decoupled from the spawn rate: the downstream
tracker needs a steady detection stream per target to confirm and hold tracks
(it coasts a track to deletion after a few missed scans), so revisiting only
when a new drone happens to spawn would starve it.

    # standalone (in-process bus; prints what it sends)
    uv run python -m sim.radar_node
    uv run python -m sim.radar_node --scan-interval 0.5 --min 3 --max 6 --seed 1

    # talk to a separate ground-station process over ZeroMQ
    uv run python -m sim.radar_node --transport zmq
"""

from __future__ import annotations

import argparse
import math
import random
import time
from datetime import datetime

from contracts.bus import Bus
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


def _make_bus(transport: str, addr: str) -> Bus:
    if transport == "zmq":
        from agent.bus import ZmqBus

        return ZmqBus(addr, bind=False)  # radar connects; the listener binds
    from agent.bus import MockBroker

    return MockBroker().endpoint("radar1")  # in-process; nobody else hears it


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous Stone-Soup radar node")
    parser.add_argument(
        "--scan-interval", type=float, default=1.0, help="seconds between radar scans"
    )
    parser.add_argument("--min", type=float, default=5.0, help="min spawn interval seconds")
    parser.add_argument("--max", type=float, default=20.0, help="max spawn interval seconds")
    parser.add_argument("--ticks", type=int, default=0, help="stop after N scans (0 = forever)")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed (default: random)")
    parser.add_argument("--transport", choices=("mock", "zmq"), default="mock")
    parser.add_argument("--addr", default="tcp://127.0.0.1:5556", help="ZeroMQ address")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    bus = _make_bus(args.transport, args.addr)

    radar = StoneSoupRadar(
        bus,
        "radar1",
        [],  # no targets yet — they appear over time
        start_time=datetime.now(),
        prob_detect=0.9,
        cull_range_m=6000.0,  # drop drones that fly out of range (bounded memory)
        seed=rng.randrange(1_000_000),
    )

    print(
        f"Radar node ({args.transport}) — scanning every {args.scan_interval:g}s, "
        f"new drone every {args.min:g}–{args.max:g}s, "
        f"publishing on {Topics.RADAR_DETECTIONS}. Ctrl-C to stop.\n"
    )
    scans = 0
    spawned = 0
    next_spawn = 0.0  # spawn the first drone on the first scan, then at random intervals
    elapsed = 0.0
    try:
        while args.ticks == 0 or scans < args.ticks:
            time.sleep(args.scan_interval)
            elapsed += args.scan_interval
            if elapsed >= next_spawn:
                spawned += 1
                radar.add_target(random_incoming(f"drone-{spawned}", rng))
                next_spawn = elapsed + rng.uniform(args.min, args.max)
                print(f"  spawn drone-{spawned}")
            scans += 1
            for det in radar.scan(datetime.now()):
                x, y, z = det.position
                rng_m = math.sqrt(x * x + y * y + z * z)
                print(
                    f"  TX [{Topics.RADAR_DETECTIONS}] t={det.timestamp:7.1f}s  "
                    f"range={rng_m:6.0f} m"
                )
    except KeyboardInterrupt:
        print("\nRadar node stopped.")


if __name__ == "__main__":
    main()
