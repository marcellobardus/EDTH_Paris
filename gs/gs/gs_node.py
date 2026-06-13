"""
Continuously-running ground-station node.

Listens for radar detections, fuses them into tracks with the multi-target
Kalman tracker, scores each track against the defended asset, and republishes
both the fused ``Track`` estimates (``/gs/tracks``) and their
``ThreatAssessment`` scores (``/gs/threats``) — so downstream (assignment) works
against clean, scored tracks rather than raw noisy detections. Prints a per-tick
threat summary so you can watch tracks form, persist, and rank. Runs until Ctrl-C.

Detections-in and the outbound stream are separate pub/sub channels on two
ZeroMQ endpoints (a single ``ZmqBus`` binds both its SUB and PUB sockets to one
address, which would collide). The node binds SUB on ``--addr`` (detections) and
PUB on ``--tracks-addr``; both ``/gs/tracks`` and ``/gs/threats`` ride that one
PUB socket (subscribers filter by topic), so a downstream consumer connects its
SUB to ``--tracks-addr``.

With ``--assign`` the node also runs the Hungarian optimizer in-process and
publishes ``/gs/assignments`` on the same outbound socket — folding the separate
``assignment_node`` into one process (no threats round-trip).

Run the listener first, then the radar in another terminal:

    uv run python -m gs.gs_node                                    # tracks + threats
    uv run python -m gs.gs_node --assign --speed 300 --range 6000  # + assignments
    uv run python -m sim.radar_node --transport zmq                # the radar
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime

from contracts.bus import SplitBus, ZmqBus
from contracts.config import ScenarioConfig
from contracts.messages import Track
from contracts.topics import Topics

from gs.assignment_publisher import AssignmentPublisher
from gs.fleet import InterceptorFleet
from gs.optimizer import AssignmentOptimizer, AssignmentResult
from gs.threat_assessor import ETA_SENTINEL, ThreatAssessor
from gs.track_publisher import TrackPublisher

log = logging.getLogger("gs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous ground-station tracker")
    parser.add_argument("--addr", default="tcp://127.0.0.1:5556", help="detections SUB address")
    parser.add_argument("--tracks-addr", default="tcp://127.0.0.1:5557", help="tracks PUB address")
    parser.add_argument("--rate", type=float, default=10.0, help="tracker tick rate (Hz)")
    parser.add_argument(
        "--target",
        type=float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        default=[0.0, 0.0, 0.0],
        help="defended-asset position for threat scoring (default: origin, where the "
        "mock world flies the drones; scenario config's target_position may differ)",
    )
    parser.add_argument(
        "--assign",
        action="store_true",
        help="also run the Hungarian optimizer in-process and publish /gs/assignments "
        "on the same outbound socket (folds in assignment_node)",
    )
    parser.add_argument(
        "--config",
        default="config/scenario_default.yaml",
        help="scenario config for the interceptor fleet (with --assign)",
    )
    parser.add_argument(
        "--ring-radius",
        type=float,
        default=300.0,
        help="interceptor defensive-ring radius around the asset (m)",
    )
    parser.add_argument(
        "--speed", type=float, default=None, help="override interceptor speed (m/s)"
    )
    parser.add_argument(
        "--range", type=float, default=None, dest="rng", help="override interceptor range (m)"
    )
    parser.add_argument(
        "--no-beat-eta",
        action="store_true",
        help="allow intercepts that land after the threat's eta",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="live re-tasking assignment (default: one-shot pre-launch burst, FR-5)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    detections_in = ZmqBus(args.addr, bind=True)  # the ground station is the stable endpoint
    tracks_out = ZmqBus(args.tracks_addr, bind=True)
    tick_interval = 1.0 / args.rate
    assessor = ThreatAssessor(tuple(args.target))

    fleet = None
    pub = None
    if args.assign:
        cfg = ScenarioConfig.from_yaml(args.config)
        fleet = InterceptorFleet.from_config(
            cfg,
            center=tuple(args.target),
            ring_radius=args.ring_radius,
            speed=args.speed,
            range_m=args.rng,
        )

        def _log_assignments(result: AssignmentResult, ts: float) -> None:
            pairs = " ".join(f"{a.interceptor_id}→{a.track_id[:8]}" for a in result.assignments)
            log.info("           assigned[%d]: %s", len(result.assignments), pairs or "(none)")

        # The GS already has the scored threats in-process, so we feed the
        # publisher directly (subscribe_threats=False) and publish on the same
        # outbound socket as /gs/tracks + /gs/threats.
        pub = AssignmentPublisher(
            tracks_out,
            fleet,
            optimizer=AssignmentOptimizer(require_beat_eta=not args.no_beat_eta),
            on_assignments=_log_assignments,
            mode="continuous" if args.continuous else "oneshot",
            subscribe_threats=False,
        )

    def on_tracks(tracks: list[Track], scenario_t: float) -> None:
        # Score every fused track and publish it as a threat. /gs/threats rides
        # the same outbound PUB socket as /gs/tracks (a PUB carries many topics).
        threats = [assessor.assess(track) for track in tracks]
        for threat in threats:
            tracks_out.publish(Topics.GS_THREATS, threat)

        # Optional in-process assignment: feed the threat snapshot to the
        # publisher and tick it (one-shot burst or continuous re-tasking).
        if pub is not None:
            pub.submit(threats)
            pub.tick(scenario_t)

        summary = (
            ", ".join(
                f"{th.track_id[:8]} score={th.threat_score:.3f} "
                f"eta={'∞' if th.eta_seconds >= ETA_SENTINEL else f'{th.eta_seconds:.0f}s'}"
                for th in sorted(threats, key=lambda th: th.threat_score, reverse=True)
            )
            or "(none confirmed yet)"
        )
        log.info("scan t=%6.1fs  threats[%d]: %s", scenario_t, len(threats), summary)

    publisher = TrackPublisher(
        SplitBus(detections_in, tracks_out),
        start_time=datetime.now(),
        on_tracks=on_tracks,
    )

    log.info(
        "tracking %s (%s) -> %s + %s (%s), asset@%s, %g Hz. Ctrl-C to stop.",
        Topics.RADAR_DETECTIONS,
        args.addr,
        Topics.GS_TRACKS,
        Topics.GS_THREATS,
        args.tracks_addr,
        tuple(args.target),
        args.rate,
    )
    if fleet is not None:
        log.info(
            "  + in-process Hungarian assignment (%s) -> %s (%d interceptors, ring r=%gm)",
            "continuous" if args.continuous else "oneshot",
            Topics.GS_ASSIGNMENTS,
            len(fleet.snapshot()),
            args.ring_radius,
        )
    next_tick = time.monotonic()
    try:
        while True:
            detections_in.spin(timeout_ms=int(tick_interval * 1000))
            now = time.monotonic()
            if now >= next_tick:
                publisher.tick()
                next_tick = now + tick_interval
    except KeyboardInterrupt:
        log.info("ground station stopped.")


if __name__ == "__main__":
    main()
