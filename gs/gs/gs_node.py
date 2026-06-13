"""
Continuously-running ground-station node.

Binds a ZeroMQ listener on ``Topics.RADAR_DETECTIONS``, prints every detection
it receives, and runs the launch decider — so you see, live, each radar hit and
the launch/hold decision per new threat. Runs until Ctrl-C.

Run the listener first, then the radar in another terminal:

    uv run python -m gs.gs_node                       # terminal 1 (listener)
    uv run python -m sim.radar_node --transport zmq   # terminal 2 (radar)
"""

from __future__ import annotations

import argparse
import math

from agent.bus import ZmqBus
from contracts.messages import RadarDetection
from contracts.topics import Topics

from gs.launch_decider import LaunchDecider, LaunchDecision


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous ground-station listener")
    parser.add_argument("--addr", default="tcp://127.0.0.1:5556", help="ZeroMQ address")
    parser.add_argument("--pool", type=int, default=3, help="interceptors available")
    args = parser.parse_args()

    bus = ZmqBus(args.addr, bind=True)        # the ground station is the stable endpoint

    def on_detection(det: RadarDetection) -> None:
        x, y, z = det.position
        rng_m = math.sqrt(x * x + y * y + z * z)
        print(f"  RX detection  t={det.timestamp:7.1f}s  range={rng_m:6.0f} m")

    def on_decision(decision: LaunchDecision) -> None:
        verb = f"LAUNCH {decision.interceptor_id}" if decision.launched else "HOLD"
        print(f"  >> {decision.threat_id}: {verb}  ({decision.reason})")

    bus.subscribe(Topics.RADAR_DETECTIONS, RadarDetection, on_detection)
    LaunchDecider(bus, interceptor_pool=args.pool, on_decision=on_decision)

    print(f"Ground station listening on {Topics.RADAR_DETECTIONS} ({args.addr}). Ctrl-C to stop.\n")
    try:
        while True:
            bus.spin(timeout_ms=200)
    except KeyboardInterrupt:
        print("\nGround station stopped.")


if __name__ == "__main__":
    main()
