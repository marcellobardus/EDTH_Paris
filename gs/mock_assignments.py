#!/usr/bin/env python3
"""
Mock GS assignment publisher — lets Team 3 develop without a running GS.

Generates synthetic Track and Assignment objects that mirror what the real GS
would produce after Kalman fusion + Hungarian optimization, then publishes them.

By default publishes once and exits (matching the real GS: one burst at launch).
Pass --repeat N to re-publish every N seconds (useful for re-tasking tests).

Transport modes:
  stdout  — one JSON line per assignment (default; no ROS2 needed)
  ros2    — publishes std_msgs/String on /gs/assignments (run inside Docker)

Usage:
    uv run python gs/mock_assignments.py
    uv run python gs/mock_assignments.py --transport ros2
    uv run python gs/mock_assignments.py --repeat 10   # re-assign every 10 s
    uv run python gs/mock_assignments.py --config config/scenario_default.yaml
"""

import argparse
import dataclasses
import json
import math
import random
import sys
import time

from contracts.config import ScenarioConfig
from contracts.messages import Assignment, Track
from contracts.topics import Topics

# ── Synthetic data generation ─────────────────────────────────────────────────

def _make_tracks(cfg: ScenarioConfig, rng: random.Random, t: float) -> list[Track]:
    tx, ty, tz = cfg.scenario.target_position
    speed_min, speed_max = cfg.shaheds.speed_mps
    r = cfg.shaheds.spawn_radius

    # Diagonal of the covariance block — 10 m sigma in position, 1 m/s in velocity
    cov = [[0.0] * 6 for _ in range(6)]
    for i in range(3):
        cov[i][i] = 100.0
    for i in range(3, 6):
        cov[i][i] = 1.0

    tracks = []
    for i in range(cfg.shaheds.count):
        angle = rng.uniform(0, 2 * math.pi)
        x = tx + r * math.cos(angle)
        y = ty + r * math.sin(angle)
        z = rng.uniform(50.0, 150.0)

        speed = rng.uniform(speed_min, speed_max)
        dx, dy, dz = tx - x, ty - y, tz - z
        norm = math.sqrt(dx * dx + dy * dy + dz * dz)
        vx, vy, vz = dx / norm * speed, dy / norm * speed, dz / norm * speed

        tracks.append(Track(
            track_id=f"t{i + 1}",
            position=(x, y, z),
            velocity=(vx, vy, vz),
            covariance=[row[:] for row in cov],
            alive=True,
            timestamp=t,
        ))
    return tracks


def _assign(cfg: ScenarioConfig, tracks: list[Track], t: float) -> list[Assignment]:
    """Greedy nearest-first assignment (mirrors the Hungarian optimizer's typical output)."""
    lx, ly, lz = cfg.interceptors.launch_position
    available = list(tracks)
    assignments = []

    for i in range(cfg.interceptors.count):
        if not available:
            break
        interceptor_id = f"i{i + 1}"

        # Pick track closest to launch position (greedy proxy for Hungarian)
        target = min(available, key=lambda tr: math.sqrt(
            (tr.position[0] - lx) ** 2 + (tr.position[1] - ly) ** 2
        ))
        available.remove(target)

        # Initial waypoint: intercept point at half the estimated flight time
        dist_to_track = math.sqrt(
            (target.position[0] - lx) ** 2 + (target.position[1] - ly) ** 2
        )
        intercept_time = dist_to_track / cfg.interceptors.speed_mps
        half_t = intercept_time / 2

        wx = target.position[0] + target.velocity[0] * half_t
        wy = target.position[1] + target.velocity[1] * half_t
        wz = target.position[2] + target.velocity[2] * half_t

        assignments.append(Assignment(
            interceptor_id=interceptor_id,
            track_id=target.track_id,
            initial_waypoint=(wx, wy, wz),
            timestamp=t,
        ))

    return assignments


# ── Transport: stdout ─────────────────────────────────────────────────────────

def run_stdout(cfg: ScenarioConfig, seed: int, repeat: float) -> None:
    rng = random.Random(seed)
    t = 0.0

    print(
        f"[mock_assignments] stdout | interceptors={cfg.interceptors.count} "
        f"tracks={cfg.shaheds.count} seed={seed}",
        file=sys.stderr,
    )

    while True:
        tracks = _make_tracks(cfg, rng, t)
        assignments = _assign(cfg, tracks, t)

        for a in assignments:
            print(json.dumps(dataclasses.asdict(a)), flush=True)

        if repeat <= 0:
            break

        time.sleep(repeat)
        t += repeat


# ── Transport: ROS2 ───────────────────────────────────────────────────────────

def run_ros2(cfg: ScenarioConfig, seed: int, repeat: float) -> None:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String

    class MockAssignmentsNode(Node):
        def __init__(self):
            super().__init__("mock_assignments")
            self._pub = self.create_publisher(String, Topics.GS_ASSIGNMENTS, 10)
            self._rng = random.Random(seed)
            self._t = 0.0
            self._repeat = repeat
            # Small delay on first publish so subscribers have time to connect
            self.create_timer(0.5, self._first_publish)
            self.get_logger().info(
                f"will publish on {Topics.GS_ASSIGNMENTS}"
                + (f" every {repeat}s" if repeat > 0 else " once")
            )

        def _first_publish(self):
            # Cancel the one-shot startup timer, then publish (and optionally loop)
            self.destroy_timer(list(self._timers)[0])
            self._publish()
            if self._repeat > 0:
                self.create_timer(self._repeat, self._publish)

        def _publish(self):
            tracks = _make_tracks(cfg, self._rng, self._t)
            assignments = _assign(cfg, tracks, self._t)
            for a in assignments:
                msg = String(data=json.dumps(dataclasses.asdict(a)))
                self._pub.publish(msg)
                self.get_logger().info(f"  {a.interceptor_id} → {a.track_id}")
            self._t += self._repeat if self._repeat > 0 else 0

    rclpy.init()
    node = MockAssignmentsNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default="config/scenario_default.yaml")
    parser.add_argument("--transport", choices=["stdout", "ros2"], default="stdout")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--repeat",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Re-publish every N seconds (0 = once only, the default)",
    )
    args = parser.parse_args()

    cfg = ScenarioConfig.from_yaml(args.config)
    seed = args.seed if args.seed is not None else cfg.scenario.seed

    if args.transport == "ros2":
        run_ros2(cfg, seed, args.repeat)
    else:
        run_stdout(cfg, seed, args.repeat)


if __name__ == "__main__":
    main()
