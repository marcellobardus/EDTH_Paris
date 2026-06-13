"""
Continuously-running ground-station node.

Listens for radar detections, fuses them into tracks with the multi-target
Kalman tracker, and republishes the fused ``Track`` estimates — so downstream
(threat scoring, assignment) works against clean tracks rather than raw noisy
detections. Prints a per-tick track summary so you can watch tracks form and
persist. Runs until Ctrl-C.

Detections-in and tracks-out are two separate pub/sub channels on two ZeroMQ
endpoints (a single ``ZmqBus`` binds both its SUB and PUB sockets to one address,
which would collide). The node binds SUB on ``--addr`` (detections) and PUB on
``--tracks-addr`` (tracks); a downstream consumer connects its SUB to
``--tracks-addr``.

Run the listener first, then the radar in another terminal:

    uv run python -m gs.gs_node                       # terminal 1 (listener)
    uv run python -m sim.radar_node --transport zmq   # terminal 2 (radar)
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

from agent.bus import ZmqBus
from contracts.bus import Bus
from contracts.messages import Track
from contracts.topics import Topics

from gs.track_publisher import TrackPublisher

T = TypeVar("T")


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
    parser.add_argument(
        "--tracks-addr", default="tcp://127.0.0.1:5557", help="tracks PUB address"
    )
    parser.add_argument("--rate", type=float, default=10.0, help="tracker tick rate (Hz)")
    args = parser.parse_args()

    detections_in = ZmqBus(args.addr, bind=True)  # the ground station is the stable endpoint
    tracks_out = ZmqBus(args.tracks_addr, bind=True)
    tick_interval = 1.0 / args.rate

    def on_tracks(tracks: list[Track]) -> None:
        summary = ", ".join(
            f"{t.track_id[:8]}@({t.position[0]:.0f},{t.position[1]:.0f},{t.position[2]:.0f})"
            for t in tracks
        )
        print(f"  tracks[{len(tracks)}]: {summary}")

    publisher = TrackPublisher(
        _SplitBus(detections_in, tracks_out),
        start_time=datetime.now(),
        on_tracks=on_tracks,
    )

    print(
        f"Ground station tracking {Topics.RADAR_DETECTIONS} ({args.addr}) -> "
        f"{Topics.GS_TRACKS} ({args.tracks_addr}), {args.rate:g} Hz. Ctrl-C to stop.\n"
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
        print("\nGround station stopped.")


if __name__ == "__main__":
    main()
