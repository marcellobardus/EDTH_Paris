#!/usr/bin/env python3
"""
Mock radar publisher — lets Team 2 develop without a running Gazebo simulation.

Simulates N Shahed targets moving toward the target and emits RadarDetection
messages using realistic noise, packet-drop, and occasional false positives.

Transport modes:
  stdout  — one JSON line per detection (default; no ROS2 needed)
  ros2    — publishes std_msgs/String on /radar/detections (run inside Docker)

Usage:
    uv run python sim/mock_radar.py
    uv run python sim/mock_radar.py --transport ros2
    uv run python sim/mock_radar.py --config config/scenario_default.yaml --seed 0
    uv run python sim/mock_radar.py --rate-hz 5 --false-positive-prob 0.05
"""

import argparse
import dataclasses
import json
import math
import random
import sys
import time

import yaml

from contracts.config import ScenarioConfig
from contracts.messages import RadarDetection
from contracts.topics import Topics

DEFAULT_RATE_HZ = 10.0
DEFAULT_FP_PROB = 0.02   # ghost detection probability per radar per tick
DEFAULT_DROP_PROB = 0.05  # packet loss probability per detection


# ── Vector helpers ────────────────────────────────────────────────────────────

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

def _norm(v):
    return math.sqrt(sum(x * x for x in v))

def _unit(v):
    n = _norm(v)
    return tuple(x / n for x in v) if n > 0 else (0.0, 0.0, 0.0)

def _scale(v, s):
    return tuple(x * s for x in v)

def _add(a, b):
    return tuple(a[i] + b[i] for i in range(len(a)))

def _dist(a, b):
    return _norm(_sub(a, b))


# ── Shahed simulation ─────────────────────────────────────────────────────────

class _Shahed:
    def __init__(self, sid: str, pos: tuple, speed: float, target: tuple):
        self.sid = sid
        self.pos = pos
        self.speed = speed
        self.vel = _scale(_unit(_sub(target, pos)), speed)
        self._target = target
        self.alive = True

    def step(self, dt: float) -> None:
        if not self.alive:
            return
        self.pos = _add(self.pos, _scale(self.vel, dt))
        if _dist(self.pos, self._target) < 10.0:
            self.alive = False


def _spawn(cfg: ScenarioConfig, rng: random.Random) -> list[_Shahed]:
    tx, ty, tz = cfg.target_position
    speed_min, speed_max = cfg.shaheds.speed_mps
    r = cfg.shaheds.spawn_radius
    spread = math.radians(cfg.shaheds.spawn_angle_spread_deg)

    shaheds = []
    for i in range(cfg.shaheds.count):
        angle = rng.uniform(0, 2 * math.pi)
        half = min(spread / 2, math.pi)
        offset = rng.uniform(-half, half)
        a = angle + offset
        x = tx + r * math.cos(a)
        y = ty + r * math.sin(a)
        z = rng.uniform(50.0, 150.0)
        speed = rng.uniform(speed_min, speed_max)
        shaheds.append(_Shahed(f"s{i + 1}", (x, y, z), speed, (tx, ty, tz)))
    return shaheds


# ── Detection logic ───────────────────────────────────────────────────────────

def _detect(radar_cfg, shahed: _Shahed, rng: random.Random,
            radar_id: str, t: float, drop_prob: float) -> RadarDetection | None:
    if not shahed.alive:
        return None
    if _dist(radar_cfg.position, shahed.pos) > radar_cfg.range:
        return None
    if rng.random() < drop_prob:
        return None

    std = radar_cfg.noise_std
    noisy = (
        shahed.pos[0] + rng.gauss(0, std),
        shahed.pos[1] + rng.gauss(0, std),
        shahed.pos[2] + rng.gauss(0, std * 0.4),  # altitude noise is lower
    )
    return RadarDetection(radar_id=radar_id, position=noisy, timestamp=t)


def _false_positive(radar_cfg, rng: random.Random,
                    radar_id: str, t: float) -> RadarDetection:
    rx, ry, _ = radar_cfg.position
    angle = rng.uniform(0, 2 * math.pi)
    dist = rng.uniform(radar_cfg.range * 0.3, radar_cfg.range * 0.9)
    std = radar_cfg.noise_std * 2
    return RadarDetection(
        radar_id=radar_id,
        position=(
            rx + dist * math.cos(angle) + rng.gauss(0, std),
            ry + dist * math.sin(angle) + rng.gauss(0, std),
            rng.uniform(30.0, 200.0),
        ),
        timestamp=t,
    )


# ── Config loading (handles nested YAML structure) ────────────────────────────

def _load_config(path: str) -> ScenarioConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    # scenario_default.yaml nests general params under a 'scenario' key
    if "scenario" in data:
        top = data.pop("scenario")
        data.update(top)
    return ScenarioConfig(**data)


# ── Simulation loop ───────────────────────────────────────────────────────────

def _tick(cfg, shaheds, rng, radar_id_prefix, t, dt, fp_prob, drop_prob):
    detections = []
    for i, radar in enumerate(cfg.radars):
        rid = f"{radar_id_prefix}{i + 1}"
        for shahed in shaheds:
            det = _detect(radar, shahed, rng, rid, t, drop_prob)
            if det:
                detections.append(det)
        if rng.random() < fp_prob:
            detections.append(_false_positive(radar, rng, rid, t))

    for shahed in shaheds:
        shahed.step(dt)

    return detections


# ── Transport: stdout ─────────────────────────────────────────────────────────

def run_stdout(cfg: ScenarioConfig, rate_hz: float, seed: int,
               fp_prob: float, drop_prob: float) -> None:
    rng = random.Random(seed)
    shaheds = _spawn(cfg, rng)
    dt = 1.0 / rate_hz
    t = 0.0

    print(
        f"[mock_radar] stdout | shaheds={cfg.shaheds.count} "
        f"radars={len(cfg.radars)} rate={rate_hz}Hz seed={seed}",
        file=sys.stderr,
    )

    while True:
        dets = _tick(cfg, shaheds, rng, "radar_", t, dt, fp_prob, drop_prob)
        for det in dets:
            print(json.dumps(dataclasses.asdict(det)), flush=True)

        if not any(s.alive for s in shaheds):
            print("[mock_radar] all shaheds reached target — restarting wave", file=sys.stderr)
            shaheds = _spawn(cfg, random.Random(seed + int(t)))

        t += dt
        time.sleep(dt)


# ── Transport: ROS2 ───────────────────────────────────────────────────────────

def run_ros2(cfg: ScenarioConfig, rate_hz: float, seed: int,
             fp_prob: float, drop_prob: float) -> None:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String

    class MockRadarNode(Node):
        def __init__(self):
            super().__init__("mock_radar")
            self._pub = self.create_publisher(String, Topics.RADAR_DETECTIONS, 10)
            self._rng = random.Random(seed)
            self._shaheds = _spawn(cfg, self._rng)
            self._dt = 1.0 / rate_hz
            self._t = 0.0
            self.create_timer(self._dt, self._tick)
            self.get_logger().info(
                f"publishing on {Topics.RADAR_DETECTIONS} at {rate_hz} Hz"
            )

        def _tick(self):
            dets = _tick(cfg, self._shaheds, self._rng, "radar_",
                         self._t, self._dt, fp_prob, drop_prob)
            for det in dets:
                msg = String(data=json.dumps(dataclasses.asdict(det)))
                self._pub.publish(msg)

            if not any(s.alive for s in self._shaheds):
                self.get_logger().info("all shaheds reached target — restarting wave")
                self._shaheds = _spawn(cfg, random.Random(seed + int(self._t)))

            self._t += self._dt

    rclpy.init()
    node = MockRadarNode()
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
    parser.add_argument("--rate-hz", type=float, default=DEFAULT_RATE_HZ)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--false-positive-prob", type=float, default=DEFAULT_FP_PROB,
                        dest="fp_prob", metavar="PROB")
    parser.add_argument("--drop-prob", type=float, default=DEFAULT_DROP_PROB,
                        dest="drop_prob", metavar="PROB")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    seed = args.seed if args.seed is not None else cfg.seed

    if args.transport == "ros2":
        run_ros2(cfg, args.rate_hz, seed, args.fp_prob, args.drop_prob)
    else:
        run_stdout(cfg, args.rate_hz, seed, args.fp_prob, args.drop_prob)


if __name__ == "__main__":
    main()
