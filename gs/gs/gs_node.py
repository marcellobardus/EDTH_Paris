"""
Continuously-running ground-station node.

Binds a ZeroMQ listener on ``Topics.RADAR_DETECTIONS``, fuses the incoming hits
into tracks with the multi-target Kalman tracker, and republishes the fused
``Track`` estimates on ``Topics.GS_TRACKS`` — so downstream (threat scoring,
assignment) works against clean tracks rather than raw noisy detections. Prints
a per-tick summary so you can watch tracks form and persist. Runs until Ctrl-C.

Run the listener first, then the radar in another terminal:

    uv run python -m gs.gs_node                       # terminal 1 (listener)
    uv run python -m sim.radar_node --transport zmq   # terminal 2 (radar)
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from agent.bus import ZmqBus
from contracts.messages import Track
from contracts.topics import Topics

from gs.track_publisher import TrackPublisher


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous ground-station tracker")
    parser.add_argument("--addr", default="tcp://127.0.0.1:5556", help="ZeroMQ address")
    parser.add_argument("--rate", type=float, default=10.0, help="tracker tick rate (Hz)")
    args = parser.parse_args()

    bus = ZmqBus(args.addr, bind=True)  # the ground station is the stable endpoint
    tick_interval = 1.0 / args.rate

    def on_tracks(tracks: list[Track]) -> None:
        summary = ", ".join(
            f"{t.track_id[:8]}@({t.position[0]:.0f},{t.position[1]:.0f},{t.position[2]:.0f})"
            for t in tracks
        )
        print(f"  tracks[{len(tracks)}]: {summary}")

    publisher = TrackPublisher(
        bus,
        start_time=datetime.now(),
        tick_interval_s=tick_interval,
        on_tracks=on_tracks,
    )

    print(
        f"Ground station tracking {Topics.RADAR_DETECTIONS} -> {Topics.GS_TRACKS} "
        f"({args.addr}, {args.rate:g} Hz). Ctrl-C to stop.\n"
    )
    next_tick = time.monotonic()
    try:
        while True:
            bus.spin(timeout_ms=int(tick_interval * 1000))
            now = time.monotonic()
            if now >= next_tick:
                publisher.tick()
                next_tick = now + tick_interval
    except KeyboardInterrupt:
        print("\nGround station stopped.")


if __name__ == "__main__":
    main()
