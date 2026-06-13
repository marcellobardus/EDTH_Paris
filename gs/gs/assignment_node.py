"""
Continuously-running assignment node (G4 Phase 3).

Subscribes to the ground station's ``ThreatAssessment`` stream on ``/gs/threats``,
runs the Hungarian optimizer over the available interceptor fleet every tick, and
publishes the resulting ``Assignment``s on ``/gs/assignments`` — replacing the
greedy ``mock_assignments.py`` with the real optimizer.

Two ZeroMQ endpoints: it connects SUB to the GS's outbound address (where
``/gs/threats`` rides, alongside ``/gs/tracks``) and binds PUB on its own
``--assignments-addr``. The interceptor fleet is built from config; its defensive
ring is centred on the defended asset (``--target``) so the geometry matches the
threats the GS scored.

Run it after the ground station (downstream first):

    uv run python -m gs.gs_node                          # tracks + threats on :5557
    uv run python -m gs.assignment_node                  # assignments on :5558
    uv run python -m sim.radar_sensor_node               # radar
    uv run python -m sim.world_node --transport zmq      # drones

This node re-solves every tick over the full READY pool (``commit=False``), so the
assignment tracks the evolving threat picture — a live re-tasking view. For the
faithful one-shot pre-launch burst, call ``assign_now(commit=True)`` once instead.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from agent.bus import ZmqBus
from contracts.config import ScenarioConfig
from contracts.topics import Topics

from gs.assignment_publisher import AssignmentPublisher
from gs.fleet import FleetUnit, InterceptorFleet, Status, ring_positions
from gs.optimizer import AssignmentOptimizer, AssignmentResult

T = TypeVar("T")
Vec3 = tuple[float, float, float]
log = logging.getLogger("gs.assign")


class _SplitBus:
    """Subscribe on one transport, publish on another (distinct ZMQ endpoints)."""

    def __init__(self, inbound: Any, outbound: Any) -> None:
        self._in = inbound
        self._out = outbound

    def subscribe(self, topic: str, msg_type: type[T], handler: Callable[[T], None]) -> None:
        self._in.subscribe(topic, msg_type, handler)

    def publish(self, topic: str, message: Any) -> None:
        self._out.publish(topic, message)


def build_fleet(
    cfg: ScenarioConfig,
    center: Vec3,
    ring_radius: float,
    speed: float | None,
    rng: float | None,
) -> InterceptorFleet:
    """Fleet from config, ring centred on the defended asset, with optional
    speed/range overrides (config interceptor kinematics are conservative)."""
    ic = cfg.interceptors
    spd = ic.speed_mps if speed is None else speed
    rg = ic.range_m if rng is None else rng
    positions = ring_positions(center, ic.count, ring_radius)
    units = [
        FleetUnit(
            interceptor_id=f"i{i + 1}",
            position=positions[i],
            velocity=(0.0, 0.0, 0.0),
            speed_mps=spd,
            range_m=rg,
            status=Status.READY,
            assigned_track_id=None,
            alive=True,
            last_update=0.0,
        )
        for i in range(ic.count)
    ]
    return InterceptorFleet(units)


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
    p.add_argument("--rate", type=float, default=2.0, help="re-solve rate (Hz)")
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
    fleet = build_fleet(cfg, tuple(args.target), args.ring_radius, args.speed, args.rng)

    threats_in = ZmqBus(args.threats_addr, bind=False)  # GS binds PUB; we connect SUB
    assignments_out = ZmqBus(args.assignments_addr, bind=True)
    bus = _SplitBus(threats_in, assignments_out)

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
    pub = AssignmentPublisher(bus, fleet, optimizer=optimizer, on_assignments=on_assignments)

    log.info(
        "assignment node: %s (%s) -> %s (%s), %d interceptors "
        "(ring r=%gm @ %s), %g Hz. Ctrl-C to stop.",
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
                pub.assign_now(commit=False)
                next_tick = now + interval
    except KeyboardInterrupt:
        log.info("assignment node stopped.")


if __name__ == "__main__":
    main()
