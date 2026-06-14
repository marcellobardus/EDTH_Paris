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
KILL_RADIUS_M = 25.0  # interceptor within this of a Shahed => kill (widened for
#                       physics tracking lag: the gz-flown interceptor trails the
#                       PN carrot, unlike the old perfect-tracking kinematic model)
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
    """A threat flying a straight, constant-speed course at the target.

    Flown by Gazebo physics (constant cmd_vel into VelocityControl), exactly like
    the interceptors: `pos`/`yaw` are refreshed each tick from the gz world pose,
    so gz is the kinematic authority and the dashboard (which reads this same
    ground-truth) stays pixel-synced with the 3D view.
    """

    def __init__(self, sid: str, pos: Vec3, speed: float, target: Vec3) -> None:
        self.id = sid
        self.pos = pos
        self.vel = _scale(_unit(_sub(target, pos)), speed)  # constant world velocity
        self.yaw = math.atan2(self.vel[1], self.vel[0])  # refreshed from gz pose
        self.alive = True
        self.reached = False  # True once it leaked through to the target
        self._target = target

    def body_velocity_command(self) -> Vec3:
        """Constant world velocity rotated into the current body frame (cmd_vel is
        body-frame). Using the live yaw keeps the world track straight even if the
        model's heading drifts."""
        wx, wy, wz = self.vel
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return (c * wx + s * wy, -s * wx + c * wy, wz)

    def check_reached(self) -> None:
        """gz owns the position; flag a leak once it arrives at the target."""
        if not self.alive:
            return
        if _dist(self.pos, self._target) < TARGET_REACH_RADIUS_M:
            self.alive = False
            self.reached = True


class Interceptor:
    """A pursuer flown by Gazebo physics via cmd_vel.

    The driver no longer integrates its motion — `pos`/`vel` are refreshed each
    tick from the gz world pose (see GzBridge), and a velocity command toward the
    latest waypoint is published back to the multicopter velocity controller. The
    Python `step()` is gone: gz is the kinematic authority for interceptors.
    """

    def __init__(self, iid: str, pos: Vec3, speed: float, max_turn_rate_deg_s: float) -> None:
        self.id = iid
        self.pos = pos
        self.vel: Vec3 = (0.0, 0.0, 0.0)
        self.yaw = 0.0  # world heading, radians (from gz orientation)
        self.alive = True
        self.waypoint: Vec3 | None = None
        self._speed = speed

    def desired_world_velocity(self) -> Vec3:
        """Velocity vector to fly toward the current waypoint, capped at cruise speed."""
        if self.waypoint is None:
            return (0.0, 0.0, 0.0)
        to_wp = _sub(self.waypoint, self.pos)
        d = _norm(to_wp)
        if d < _EPS:
            return (0.0, 0.0, 0.0)
        # Ease off in the last few metres so we don't overshoot the carrot.
        speed = self._speed if d > self._speed else d
        return _scale(_unit(to_wp), speed)

    def body_velocity_command(self) -> Vec3:
        """desired_world_velocity rotated into the body frame (cmd_vel is body-frame).

        Vertical is shared between frames for near-level flight; only the
        horizontal vector is rotated by -yaw.
        """
        wx, wy, wz = self.desired_world_velocity()
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        bx = c * wx + s * wy
        by = -s * wx + c * wy
        return (bx, by, wz)


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
        # Interceptors are flown by gz physics; their pos/vel are refreshed from
        # the world pose before this call, so we only integrate the shaheds here.
        self.t += dt

        leaked: list[str] = []
        for sh in self.shaheds:
            was_alive = sh.alive
            sh.check_reached()  # gz flies it; we only watch for the leak
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


# ── Gazebo bridge ───────────────────────────────────────────────────────────────
#
# Closes the physics half of the loop the agents can't see:
#   - interceptors are flown by gz's MulticopterVelocityControl: we publish
#     enable=true + a body-frame cmd_vel toward each waypoint, and read their
#     true world pose back from /world/<world>/pose/info (NOT /model/.../odometry,
#     which does not report world z — that mistake cost us hours).
#   - shaheds are <static> SDF models with no controller, so we teleport them to
#     the Python-kinematic pose each tick via the set_pose service.
#
# ID convention bridged here: agent id i{n} <-> gz model interceptor_{n};
# track id t{n} <-> gz model shahed_{n}.

GZ_WORLD = "intercept_scenario"
# Just enough climb-out to clear the ground before chasing; above this the
# interceptor homes fully in 3D on the waypoint (which sits at the target's
# altitude), so it can actually close the vertical gap to a shahed.
CRUISE_ALTITUDE_M = 20.0
# Shaheds are placed at their (randomised) spawn for this long via set_pose so the
# gz model lands on the Python spawn point; after this they fly purely on physics
# (a constant cmd_vel into gz VelocityControl). Brief — just covers gz model spawn.
SHAHED_PLACEMENT_S = 0.6


def _yaw_from_quat(w: float, x: float, y: float, z: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _quat_from_yaw(yaw: float) -> tuple[float, float, float, float]:
    """(w, x, y, z) for a yaw-only rotation about +Z."""
    return (math.cos(yaw * 0.5), 0.0, 0.0, math.sin(yaw * 0.5))


class GzBridge:
    """Thin gz-transport seam: fly interceptors via cmd_vel, teleport shaheds."""

    def __init__(self, world: SimWorld) -> None:
        from gz.msgs10.boolean_pb2 import Boolean
        from gz.msgs10.pose_pb2 import Pose
        from gz.msgs10.pose_v_pb2 import Pose_V
        from gz.msgs10.twist_pb2 import Twist
        from gz.transport13 import Node

        self._Boolean = Boolean
        self._Pose = Pose
        self._Pose_V = Pose_V
        self._Twist = Twist
        self._world = world
        self._node = Node()

        # i{n} -> interceptor_{n} (and reverse, by gz model name).
        self._gz_name = {f"i{n}": f"interceptor_{n}" for n in range(1, len(world.interceptors) + 1)}
        self._id_by_gz = {v: k for k, v in self._gz_name.items()}
        # t{n} -> shahed_{n} (and reverse).
        self._shahed_gz = {f"t{n}": f"shahed_{n}" for n in range(1, len(world.shaheds) + 1)}
        self._shahed_id_by_gz = {v: k for k, v in self._shahed_gz.items()}

        # Velocity command per interceptor (consumed by the gz VelocityControl
        # system on each model — no enable handshake, unlike the old multicopter
        # controller this replaced).
        self._cmd_pub = {}
        for itc in world.interceptors:
            ns = self._gz_name[itc.id]
            self._cmd_pub[itc.id] = self._node.advertise(f"/{ns}/cmd_vel", Twist)

        # Shaheds fly the same way now (physics, not teleport): a constant cmd_vel
        # into VelocityControl. Stash each one's spawn pose so we can seat the gz
        # model on its randomised spawn before handing it to physics.
        self._shahed_cmd_pub = {}
        self._shahed_spawn: dict[str, Vec3] = {}
        self._shahed_spawn_yaw: dict[str, float] = {}
        for sh in world.shaheds:
            ns = self._shahed_gz[sh.id]
            self._shahed_cmd_pub[sh.id] = self._node.advertise(f"/{ns}/cmd_vel", Twist)
            self._shahed_spawn[sh.id] = sh.pos
            self._shahed_spawn_yaw[sh.id] = sh.yaw

        # Captured the first time each interceptor's gz pose is seen, so a reset can
        # seat it back on its launch pad (the shared launch_position would stack them).
        self._itc_spawn: dict[str, tuple[Vec3, float]] = {}

        # World pose feed — refreshes interceptor truth in place.
        self._node.subscribe(Pose_V, f"/world/{GZ_WORLD}/pose/info", self._on_pose_v)
        # Batch all shahed teleports into ONE service call: the gz request is
        # blocking and runs on the rclpy executor thread, so 4 calls/tick starved
        # the physics step. One Pose_V call keeps the tick cheap.
        self._set_pose_vec_srv = f"/world/{GZ_WORLD}/set_pose_vector"

    # -- truth in <- gz (interceptors + shaheds are both gz-flown) ----------
    def _on_pose_v(self, msg: Any) -> None:
        itc_by_id = {itc.id: itc for itc in self._world.interceptors}
        sh_by_id = {sh.id: sh for sh in self._world.shaheds}
        for p in msg.pose:
            body = itc_by_id.get(self._id_by_gz.get(p.name, "")) or sh_by_id.get(
                self._shahed_id_by_gz.get(p.name, "")
            )
            if body is None:
                continue
            pos, o = p.position, p.orientation
            body.pos = (pos.x, pos.y, pos.z)
            body.yaw = _yaw_from_quat(o.w, o.x, o.y, o.z)
            if p.name in self._id_by_gz and body.id not in self._itc_spawn:
                self._itc_spawn[body.id] = (body.pos, body.yaw)  # launch pad pose

    # -- interceptor control out -> gz -------------------------------------
    def publish_cmd_vel(self) -> None:
        for itc in self._world.interceptors:
            t = self._Twist()
            if itc.alive:
                # Hold cruise altitude if we're still below it and have somewhere to go.
                bx, by, bz = itc.body_velocity_command()
                if itc.waypoint is not None and itc.pos[2] < CRUISE_ALTITUDE_M - 5.0:
                    bz = max(bz, 6.0)  # prioritise climbing out before chasing
                t.linear.x, t.linear.y, t.linear.z = bx, by, bz
            # Expended interceptor: keep commanding zero velocity so VelocityControl
            # freezes it in place (otherwise it coasts on the last command forever).
            self._cmd_pub[itc.id].publish(t)

    # -- shahed control out -> gz ------------------------------------------
    def publish_shahed_cmd_vel(self) -> None:
        """Constant velocity command → VelocityControl flies each shahed straight.
        The body-frame command uses the live gz yaw, so the world track is straight
        regardless of the model's heading."""
        for sh in self._world.shaheds:
            t = self._Twist()
            if sh.alive:
                bx, by, bz = sh.body_velocity_command()
                t.linear.x, t.linear.y, t.linear.z = bx, by, bz
            # Dead shahed: zero velocity freezes it (don't coast past the kill point).
            self._shahed_cmd_pub[sh.id].publish(t)

    def place_shaheds(self) -> None:
        """Seat each shahed on its randomised spawn (oriented along its velocity) for
        a brief window so gz instantiates the model in the right place; after that,
        physics flies it entirely (constant cmd_vel) and gz owns its pose."""
        if self._world.t > SHAHED_PLACEMENT_S:
            return
        req = self._Pose_V()
        any_alive = False
        for sh in self._world.shaheds:
            gz_name = self._shahed_gz.get(sh.id)
            if gz_name is None or not sh.alive:
                continue
            any_alive = True
            sx, sy, sz = self._shahed_spawn[sh.id]
            p = req.pose.add()
            p.name = gz_name
            p.position.x, p.position.y, p.position.z = sx, sy, sz
            qw, qx, qy, qz = _quat_from_yaw(self._shahed_spawn_yaw[sh.id])
            p.orientation.w, p.orientation.x, p.orientation.y, p.orientation.z = qw, qx, qy, qz
        if not any_alive:
            return
        try:
            self._node.request(self._set_pose_vec_srv, req, self._Pose_V, self._Boolean, 50)
        except Exception:
            pass

    # -- reset --------------------------------------------------------------
    def rebind(self, world: SimWorld) -> None:
        """Point the bridge at a fresh world (after a reset) and rebuild the shahed
        spawn caches. Publisher handles and interceptor launch pads are reused —
        same models, same ids."""
        self._world = world
        self._shahed_spawn = {sh.id: sh.pos for sh in world.shaheds}
        self._shahed_spawn_yaw = {sh.id: sh.yaw for sh in world.shaheds}

    def reset_bodies(self) -> None:
        """Snap every model back to spawn: interceptors onto their launch pads,
        shaheds onto their fresh spawn. place_shaheds keeps shaheds seated through
        the new placement window; cmd_vel takes over after."""
        req = self._Pose_V()
        for itc in self._world.interceptors:
            sp = self._itc_spawn.get(itc.id)
            if sp is None:
                continue
            (px, py, pz), yaw = sp
            p = req.pose.add()
            p.name = self._gz_name[itc.id]
            p.position.x, p.position.y, p.position.z = px, py, pz
            qw, qx, qy, qz = _quat_from_yaw(yaw)
            p.orientation.w, p.orientation.x, p.orientation.y, p.orientation.z = qw, qx, qy, qz
        for sh in self._world.shaheds:
            sx, sy, sz = self._shahed_spawn[sh.id]
            p = req.pose.add()
            p.name = self._shahed_gz[sh.id]
            p.position.x, p.position.y, p.position.z = sx, sy, sz
            qw, qx, qy, qz = _quat_from_yaw(self._shahed_spawn_yaw[sh.id])
            p.orientation.w, p.orientation.x, p.orientation.y, p.orientation.z = qw, qx, qy, qz
        try:
            self._node.request(self._set_pose_vec_srv, req, self._Pose_V, self._Boolean, 50)
        except Exception:
            pass


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
            self._gz = GzBridge(self.world)

            # agent --waypoint--> driver
            for itc in self.world.interceptors:
                topic = Topics.waypoint_command(itc.id)
                self.create_subscription(String, topic, self._make_waypoint_cb(itc.id), 10)

            # dashboard --reset--> driver (respawn the scenario)
            self.create_subscription(String, Topics.RESET, self._on_reset, 10)

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
            # Push velocity commands (interceptors + shaheds) to gz VelocityControl
            # at the ground-truth rate — cheap non-blocking publishes.
            self.create_timer(1.0 / GROUND_TRUTH_HZ, self._drive_gz)
            # Initial shahed placement (blocking set_pose) on its own slower timer,
            # off the hot path; it self-disables after the placement window.
            self.create_timer(0.1, self._gz.place_shaheds)
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

        def _drive_gz(self) -> None:
            # Push velocity commands to gz VelocityControl: interceptors home on
            # their waypoints, shaheds fly a constant-velocity straight line.
            self._gz.publish_cmd_vel()
            self._gz.publish_shahed_cmd_vel()

        def _pub_ground_truth(self) -> None:
            self._gt_pub.publish(String(data=_encode_list(self.world.ground_truth())))

        def _pub_radar(self) -> None:
            dets = self.world.radar_detections()
            if dets:
                self._radar_pub.publish(String(data=_encode_list(dets)))

        def _pub_tracks(self) -> None:
            self._track_pub.publish(String(data=_encode_list(self.world.tracks())))

        def _publish_assignments(self) -> None:
            if not self._emit_gs:
                return
            assignments = self.world.initial_assignments()
            self._assign_pub.publish(String(data=_encode_list(assignments)))
            for a in assignments:
                self.get_logger().info(f"ASSIGN {a.interceptor_id} -> {a.track_id}")

        def _pub_assignments_once(self) -> None:
            # Fire once: cancel our own timer, then publish the latched launch plan.
            if self._assign_timer is not None:
                self.destroy_timer(self._assign_timer)
                self._assign_timer = None
            self._publish_assignments()

        def _on_reset(self, _msg: Any) -> None:
            # Respawn a fresh scenario (same seed) in place: new threats, interceptors
            # back on their pads, clock + kills cleared, launch plan re-issued.
            self.world = SimWorld(cfg, seed, kill_radius)
            self._gz.rebind(self.world)
            self._gz.reset_bodies()
            self._publish_assignments()
            self.get_logger().info("RESET: scenario respawned")

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
