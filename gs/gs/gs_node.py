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

Run the listener first, then the radar in another terminal:

    uv run python -m gs.gs_node                       # terminal 1 (listener)
    uv run python -m sim.radar_node --transport zmq   # terminal 2 (radar)
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

from agent.bus import ZmqBus
from contracts.bus import Bus
from contracts.messages import Track
from contracts.topics import Topics

from gs.threat_assessor import ETA_SENTINEL, ThreatAssessor
from gs.track_publisher import TrackPublisher

T = TypeVar("T")
log = logging.getLogger("gs")


class _SplitBus:
    """A ``Bus`` that subscribes on one transport and publishes on another — so
    the node can receive detections and emit tracks on distinct ZeroMQ
    endpoints."""

    def __init__(self, inbound: Bus, outbound: Bus) -> None:
        self._in = inbound
        self._out = outbound

    def subscribe(self, topic: str, msg_type: type[T], handler: Callable[[T], None]) -> None:
        self._in.subscribe(topic, msg_type, handler)

    def publish(self, topic: str, message: Any) -> None:
        self._out.publish(topic, message)


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

    def on_tracks(tracks: list[Track], scenario_t: float) -> None:
        # Score every fused track and publish it as a threat. /gs/threats rides
        # the same outbound PUB socket as /gs/tracks (a PUB carries many topics).
        threats = [assessor.assess(track) for track in tracks]
        for threat in threats:
            tracks_out.publish(Topics.GS_THREATS, threat)
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
        _SplitBus(detections_in, tracks_out),
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
