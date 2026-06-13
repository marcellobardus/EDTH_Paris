#!/usr/bin/env python3
"""
Sim driver — the kinematic loop that *plugs the simulation into the agents*.

`world.py` only launches Gazebo for visualization; nothing there closes the
control loop. This node is that loop. It is the single authoritative kinematic
truth and it closes two wires the agents depend on:

    agent --/interceptors/{id}/waypoint--> [DRIVER] --/simulation/ground_truth--> agent
                                              |
              /radar/detections, /simulation/engagement  (and, until the real
              GS lands, a perfect-sensor /gs/tracks + one-shot /gs/assignments)

Why pure-Python kinematics instead of Gazebo physics: the hackathon result is a
*coordination* claim (Situation B beats A), not flight-dynamics fidelity. The
agents already speak ROS2 JSON-over-`std_msgs/String`; a kinematic integrator
closes the loop today without a gz<->ROS bridge or model controller plugins.

ID convention (must match GS + agent): interceptors are ``i1..iN`` (the agent's
``INTERCEPTOR_ID``); each Shahed is identified end-to-end by its *track* id
``t1..tN`` — i.e. in this integrated sim the track **is** the Shahed, so
``track_id == ground-truth id == engagement target``, matching what
``gs/mock_assignments.py`` already emits (``i{n} -> t{n}``).

Wire format: identical JSON envelope the agents use (``agent/serde.py`` ==
``json.dumps(dataclasses.asdict(obj))``); we inline it here rather than import
Team 3 code, mirroring ``mock_radar.py`` / ``mock_assignments.py``.

Run:  python3 -m sim.driver [--config ...] [--no-gs] [--seed N]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from contracts.config import ScenarioConfig
from contracts.messages import (
    Assignment,
    EngagementEvent,
    GroundTruthObject,
    RadarDetection,
    Track,
)
from contracts.topics import Topics

# Engagement geometry (no config field exists for these; sensible defaults).
KILL_RADIUS_M = 15.0  # interceptor within this of a Shahed => kill
TARGET_REACH_RADIUS_M = 10.0  # Shahed within this of target => leak (matches mock_radar)

# Loop rates.
PHYS_HZ = 50.0  # integration step
GROUND_TRUTH_HZ = 20.0  # pose feedback to agents (>= guidance 10 Hz)
RADAR_HZ = 10.0  # detections to the GS
TRACK_HZ = 10.0  # perfect-sensor track stand-in (--no-gs disables)
ASSIGN_DELAY_S = 0.7  # let subscribers connect before the one-shot assignment

Vec3 = tuple[float, float, float]
_EPS = 1e-9


# ── Vector helpers (typed; dependency-free — same idiom as mock_radar) ─────────


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _dist(a: Vec3, b: Vec3) -> float:
    return _norm(_sub(a, b))


def _unit(a: Vec3) -> Vec3:
    n = _norm(a)
    return _scale(a, 1.0 / n) if n > _EPS else (0.0, 0.0, 0.0)


def _rotate_toward(heading: Vec3, desired: Vec3, max_angle: float) -> Vec3:
    """Unit `heading` rotated toward unit `desired` by at most `max_angle` rad."""
    cos = max(-1.0, min(1.0, _dot(heading, desired)))
    ang = math.acos(cos)
    if ang <= max_angle or ang < _EPS:
        return desired
    axis = _cross(heading, desired)
    if _norm(axis) < _EPS:  # (anti)parallel — no unique rotation plane
        return desired
    # slerp heading -> desired, stopped at max_angle (sin(ang) > 0 here).
    t = max_angle / ang
    s = math.sin(ang)
    return _unit(
        _add(_scale(heading, math.sin((1.0 - t) * ang) / s), _scale(desired, math.sin(t * ang) / s))
    )


# ── Kinematic bodies (pure; no rclpy, so unit-testable headless) ───────────────


class Shahed:
    """A threat flying a straight, constant-speed course at the target."""

    def __init__(self, sid: str, pos: Vec3, speed: float, target: Vec3) -> None:
        self.id = sid
        self.pos = pos
        self.vel = _scale(_unit(_sub(target, pos)), speed)
        self.alive = True
        self.reached = False  # True once it leaked through to the target
        self._target = target

    def step(self, dt: float) -> None:
        if not self.alive:
            return
        self.pos = _add(self.pos, _scale(self.vel, dt))
        if _dist(self.pos, self._target) < TARGET_REACH_RADIUS_M:
            self.alive = False
            self.reached = True


class Interceptor:
    """A pursuer that chases the latest waypoint at fixed speed, rate-limited."""

    def __init__(self, iid: str, pos: Vec3, speed: float, max_turn_rate_deg_s: float) -> None:
        self.id = iid
        self.pos = pos
        self.vel: Vec3 = (0.0, 0.0, 0.0)
        self.alive = True
        self.waypoint: Vec3 | None = None
        self._speed = speed
        self._max_turn = math.radians(max_turn_rate_deg_s)

    def step(self, dt: float) -> None:
        if not self.alive or self.waypoint is None:
            return
        to_wp = _sub(self.waypoint, self.pos)
        if _norm(to_wp) < _EPS:
            return
        desired = _unit(to_wp)
        speed = _norm(self.vel)
        if speed < _EPS:
            new_dir = desired  # just launched: head straight at the carrot
        else:
            new_dir = _rotate_toward(_unit(self.vel), desired, self._max_turn * dt)
        self.vel = _scale(new_dir, self._speed)
        self.pos = _add(self.pos, _scale(self.vel, dt))


@dataclass
class StepResult:
    engagements: list[EngagementEvent]  # kills confirmed this tick
    leaked: list[str]  # Shahed ids that reached the target this tick


# ── The world ──────────────────────────────────────────────────────────────────


class SimWorld:
    """Authoritative kinematic state for all bodies + the sim clock."""

    def __init__(self, cfg: ScenarioConfig, seed: int, kill_radius: float = KILL_RADIUS_M) -> None:
        self.cfg = cfg
        self.t = 0.0
        self._rng = random.Random(seed)
        self._kill_radius = kill_radius
        self.shaheds = self._spawn_shaheds()
        self.interceptors = [
            Interceptor(
                f"i{n}",
                cfg.interceptors.launch_position,
                cfg.interceptors.speed_mps,
                cfg.interceptors.max_turn_rate_deg_s,
            )
            for n in range(1, cfg.interceptors.count + 1)
        ]

    def _spawn_shaheds(self) -> list[Shahed]:
        tx, ty, tz = self.cfg.scenario.target_position
        smin, smax = self.cfg.shaheds.speed_mps
        r = self.cfg.shaheds.spawn_radius
        spread = math.radians(self.cfg.shaheds.spawn_angle_spread_deg)
        out: list[Shahed] = []
        for i in range(self.cfg.shaheds.count):
            base = self._rng.uniform(0, 2 * math.pi)
            half = min(spread / 2, math.pi)
            a = base + self._rng.uniform(-half, half)
            pos = (tx + r * math.cos(a), ty + r * math.sin(a), self._rng.uniform(50.0, 150.0))
            speed = self._rng.uniform(smin, smax)
            out.append(Shahed(f"t{i + 1}", pos, speed, (tx, ty, tz)))
        return out

    def set_waypoint(self, interceptor_id: str, point: Vec3) -> None:
        for itc in self.interceptors:
            if itc.id == interceptor_id:
                itc.waypoint = point
                return

    def step(self, dt: float) -> StepResult:
        self.t += dt
        for itc in self.interceptors:
            itc.step(dt)

        leaked: list[str] = []
        for sh in self.shaheds:
            was_alive = sh.alive
            sh.step(dt)
            if was_alive and sh.reached:
                leaked.append(sh.id)

        engagements: list[EngagementEvent] = []
        for itc in self.interceptors:
            if not itc.alive:
                continue
            for sh in self.shaheds:
                if sh.alive and _dist(itc.pos, sh.pos) <= self._kill_radius:
                    sh.alive = False
                    itc.alive = False  # one interceptor, one shot — expended on kill
                    engagements.append(
                        EngagementEvent(
                            interceptor_id=itc.id,
                            track_id=sh.id,
                            success=True,
                            position=sh.pos,
                            timestamp=self.t,
                        )
                    )
                    break  # this interceptor is spent
        return StepResult(engagements, leaked)

    # -- snapshots emitted onto the bus -------------------------------------

    def ground_truth(self) -> list[GroundTruthObject]:
        objs = [
            GroundTruthObject(itc.id, "interceptor", itc.pos, itc.vel, itc.alive)
            for itc in self.interceptors
        ]
        objs += [
            GroundTruthObject(sh.id, "shahed", sh.pos, sh.vel, sh.alive) for sh in self.shaheds
        ]
        return objs

    def radar_detections(self, drop_prob: float = 0.05) -> list[RadarDetection]:
        """Noisy hits from each radar (feeds the real GS). No id leaks per contract."""
        dets: list[RadarDetection] = []
        for ridx, radar in enumerate(self.cfg.radars):
            rid = f"radar_{ridx + 1}"
            for sh in self.shaheds:
                if not sh.alive or _dist(radar.position, sh.pos) > radar.range:
                    continue
                if self._rng.random() < drop_prob:
                    continue
                std = radar.noise_std
                noisy = (
                    sh.pos[0] + self._rng.gauss(0, std),
                    sh.pos[1] + self._rng.gauss(0, std),
                    sh.pos[2] + self._rng.gauss(0, std * 0.4),
                )
                dets.append(RadarDetection(radar_id=rid, position=noisy, timestamp=self.t))
        return dets

    def tracks(self) -> list[Track]:
        """Perfect-sensor track stand-in (until Team 2's fusion lands)."""
        cov = [[0.0] * 6 for _ in range(6)]
        for i in range(3):
            cov[i][i] = 25.0  # ~5 m position sigma
            cov[i + 3][i + 3] = 1.0
        return [
            Track(sh.id, sh.pos, sh.vel, [row[:] for row in cov], True, self.t)
            for sh in self.shaheds
            if sh.alive
        ]

    def initial_assignments(self) -> list[Assignment]:
        """Greedy nearest-first launch plan from current truth (GS stand-in)."""
        lx, ly, _ = self.cfg.interceptors.launch_position
        speed = self.cfg.interceptors.speed_mps
        available = [sh for sh in self.shaheds if sh.alive]
        out: list[Assignment] = []
        for n in range(1, self.cfg.interceptors.count + 1):
            if not available:
                break
            tgt = min(available, key=lambda s: math.hypot(s.pos[0] - lx, s.pos[1] - ly))
            available.remove(tgt)
            half_t = math.hypot(tgt.pos[0] - lx, tgt.pos[1] - ly) / speed / 2.0
            wp = _add(tgt.pos, _scale(tgt.vel, half_t))
            out.append(Assignment(f"i{n}", tgt.id, wp, self.t))
        return out


# ── JSON envelope (identical to agent/serde.py — inlined, see module docstring) ─


def _encode(obj: Any) -> str:
    return json.dumps(dataclasses.asdict(obj))


def _encode_list(objs: Sequence[Any]) -> str:
    return json.dumps([dataclasses.asdict(o) for o in objs])


# ── ROS2 node ──────────────────────────────────────────────────────────────────


def _run(cfg: ScenarioConfig, seed: int, emit_gs: bool, kill_radius: float) -> None:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import (
        QoSDurabilityPolicy,
        QoSHistoryPolicy,
        QoSProfile,
        QoSReliabilityPolicy,
    )
    from std_msgs.msg import String

    # Assignments are latched: a late-joining agent still gets the one-shot launch
    # plan. MUST match the agent's subscriber (TRANSIENT_LOCAL + RELIABLE), or DDS
    # silently drops it — this is exactly the bug in mock_assignments.py's default QoS.
    latched = QoSProfile(
        depth=1,
        history=QoSHistoryPolicy.KEEP_LAST,
        reliability=QoSReliabilityPolicy.RELIABLE,
        durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    )

    class SimDriverNode(Node):  # type: ignore[misc]  # rclpy.Node is untyped
        def __init__(self) -> None:
            super().__init__("sim_driver")
            self.world = SimWorld(cfg, seed, kill_radius)
            self._emit_gs = emit_gs

            # agent --waypoint--> driver
            for itc in self.world.interceptors:
                topic = Topics.waypoint_command(itc.id)
                self.create_subscription(String, topic, self._make_waypoint_cb(itc.id), 10)

            # driver --> bus
            self._gt_pub = self.create_publisher(String, Topics.GROUND_TRUTH, 10)
            self._radar_pub = self.create_publisher(String, Topics.RADAR_DETECTIONS, 10)
            self._engage_pub = self.create_publisher(String, Topics.ENGAGEMENT, 10)
            if emit_gs:
                self._track_pub = self.create_publisher(String, Topics.GS_TRACKS, 10)
                self._assign_pub = self.create_publisher(String, Topics.GS_ASSIGNMENTS, latched)

            self.create_timer(1.0 / PHYS_HZ, self._step)
            self.create_timer(1.0 / GROUND_TRUTH_HZ, self._pub_ground_truth)
            self.create_timer(1.0 / RADAR_HZ, self._pub_radar)
            self._assign_timer: Any = None
            if emit_gs:
                self.create_timer(1.0 / TRACK_HZ, self._pub_tracks)
                self._assign_timer = self.create_timer(ASSIGN_DELAY_S, self._pub_assignments_once)

            self.get_logger().info(
                f"sim_driver up: {len(self.world.interceptors)} interceptors, "
                f"{len(self.world.shaheds)} shaheds, gs_standin={emit_gs}, "
                f"kill_radius={kill_radius} m"
            )

        def _make_waypoint_cb(self, interceptor_id: str) -> Any:
            def _cb(msg: Any) -> None:
                raw = json.loads(msg.data)
                p = raw["position"]
                self.world.set_waypoint(interceptor_id, (p[0], p[1], p[2]))

            return _cb

        def _step(self) -> None:
            result = self.world.step(1.0 / PHYS_HZ)
            for ev in result.engagements:
                self._engage_pub.publish(String(data=_encode(ev)))
                self.get_logger().info(
                    f"KILL {ev.interceptor_id} -> {ev.track_id} @ t={ev.timestamp:.1f}s"
                )
            for sid in result.leaked:
                self.get_logger().info(f"LEAK {sid} reached target @ t={self.world.t:.1f}s")

        def _pub_ground_truth(self) -> None:
            self._gt_pub.publish(String(data=_encode_list(self.world.ground_truth())))

        def _pub_radar(self) -> None:
            dets = self.world.radar_detections()
            if dets:
                self._radar_pub.publish(String(data=_encode_list(dets)))

        def _pub_tracks(self) -> None:
            self._track_pub.publish(String(data=_encode_list(self.world.tracks())))

        def _pub_assignments_once(self) -> None:
            # Fire once: cancel our own timer, then publish the latched launch plan.
            if self._assign_timer is not None:
                self.destroy_timer(self._assign_timer)
                self._assign_timer = None
            assignments = self.world.initial_assignments()
            self._assign_pub.publish(String(data=_encode_list(assignments)))
            for a in assignments:
                self.get_logger().info(f"ASSIGN {a.interceptor_id} -> {a.track_id}")

    rclpy.init()
    node = SimDriverNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", default="config/scenario_default.yaml")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--kill-radius", type=float, default=KILL_RADIUS_M)
    parser.add_argument(
        "--no-gs",
        action="store_true",
        help="Don't emit the /gs/tracks + /gs/assignments stand-ins (use when the real GS runs).",
    )
    args = parser.parse_args()

    cfg = ScenarioConfig.from_yaml(args.config)
    seed = args.seed if args.seed is not None else cfg.scenario.seed
    _run(cfg, seed, emit_gs=not args.no_gs, kill_radius=args.kill_radius)


if __name__ == "__main__":
    main()
