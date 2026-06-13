"""
Continuously-running assignment node (G4 Phase 3).

Subscribes to the ground station's ``ThreatAssessment`` stream on ``/gs/threats``
and publishes ``Assignment``s on ``/gs/assignments`` — replacing the greedy
``mock_assignments.py`` with the real Hungarian optimizer.

Two ZeroMQ endpoints: it connects SUB to the GS's outbound address (where
``/gs/threats`` rides, alongside ``/gs/tracks``) and binds PUB on its own
``--assignments-addr``. The interceptor fleet is built from config; its defensive
ring is centred on the defended asset (``--target``) so the geometry matches the
threats the GS scored.

By default it does the **one-shot pre-launch burst** (FR-5): wait for the threat
picture to stabilise, solve once committing the chosen units, then republish that
plan. Pass ``--continuous`` for a live re-tasking view that re-solves each tick.

Run it after the ground station (downstream first):

    uv run python -m gs.gs_node                          # tracks + threats on :5557
    uv run python -m gs.assignment_node                  # assignments on :5558
    uv run python -m sim.radar_sensor_node               # radar
    uv run python -m sim.world_node --transport zmq      # drones
"""

from __future__ import annotations

import argparse
import logging
import time

from contracts.bus import SplitBus, ZmqBus
from contracts.config import ScenarioConfig
from contracts.topics import Topics

from gs.assignment_publisher import AssignmentPublisher
from gs.fleet import InterceptorFleet
from gs.optimizer import AssignmentOptimizer, AssignmentResult

log = logging.getLogger("gs.assign")


def main() -> None:
    p = argparse.ArgumentParser(description="Continuous Hungarian assignment node")
    p.add_argument(
        "--threats-addr",
        default="tcp://127.0.0.1:5557",
        help="GS threats/tracks SUB address (we connect)",
    )
    p.add_argument(
        "--assignments-addr",
        default="tcp://127.0.0.1:5558",
        help="assignments PUB address (we bind)",
    )
    p.add_argument("--config", default="config/scenario_default.yaml")
    p.add_argument(
        "--target",
        type=float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        default=[0.0, 0.0, 0.0],
        help="defended asset; the interceptor ring centres here "
        "(default origin, where the mock world flies the drones)",
    )
    p.add_argument(
        "--ring-radius",
        type=float,
        default=300.0,
        help="defensive-ring radius for interceptor sites (m)",
    )
    p.add_argument("--speed", type=float, default=None, help="override interceptor speed (m/s)")
    p.add_argument(
        "--range", type=float, default=None, dest="rng", help="override interceptor range (m)"
    )
    p.add_argument("--rate", type=float, default=2.0, help="tick rate (Hz)")
    p.add_argument(
        "--continuous",
        action="store_true",
        help="live re-tasking: re-solve each tick (default: one-shot pre-launch burst)",
    )
    p.add_argument(
        "--no-beat-eta",
        action="store_true",
        help="allow intercepts that land after the threat's eta",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = ScenarioConfig.from_yaml(args.config)
    fleet = InterceptorFleet.from_config(
        cfg,
        center=tuple(args.target),
        ring_radius=args.ring_radius,
        speed=args.speed,
        range_m=args.rng,
    )

    threats_in = ZmqBus(args.threats_addr, bind=False)  # GS binds PUB; we connect SUB
    assignments_out = ZmqBus(args.assignments_addr, bind=True)
    bus = SplitBus(threats_in, assignments_out)

    def on_assignments(result: AssignmentResult, ts: float) -> None:
        pairs = ", ".join(f"{a.interceptor_id}→{a.track_id[:8]}" for a in result.assignments)
        log.info(
            "t=%6.1fs  assigned[%d]: %s  held=%d uncovered=%d",
            ts,
            len(result.assignments),
            pairs or "(none)",
            len(result.held_interceptors),
            len(result.uncovered_threats),
        )

    optimizer = AssignmentOptimizer(require_beat_eta=not args.no_beat_eta)
    mode = "continuous" if args.continuous else "oneshot"
    pub = AssignmentPublisher(
        bus, fleet, optimizer=optimizer, on_assignments=on_assignments, mode=mode
    )

    log.info(
        "assignment node (%s): %s (%s) -> %s (%s), %d interceptors "
        "(ring r=%gm @ %s), %g Hz. Ctrl-C to stop.",
        mode,
        Topics.GS_THREATS,
        args.threats_addr,
        Topics.GS_ASSIGNMENTS,
        args.assignments_addr,
        len(fleet.snapshot()),
        args.ring_radius,
        tuple(args.target),
        args.rate,
    )
    interval = 1.0 / args.rate
    next_tick = time.monotonic()
    try:
        while True:
            threats_in.spin(timeout_ms=int(interval * 1000))
            now = time.monotonic()
            if now >= next_tick:
                pub.tick()
                next_tick = now + interval
    except KeyboardInterrupt:
        log.info("assignment node stopped.")


if __name__ == "__main__":
    main()
