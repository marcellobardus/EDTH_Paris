"""
Proportional-navigation guidance (FR-6, architecture §7). Pure NumPy, ROS-free.

CLAUDE.md gives the canonical form:
    omega = cross(R, R_dot) / dot(R, R)   # LOS angular-rate vector
    a_cmd = N * self_speed * omega         # N ~ 3-5
`omega` is the LOS *rotation axis* (perpendicular to the engagement plane), so
adding `N*v*omega` straight onto the velocity would push out-of-plane and not
steer. We therefore apply that same magnitude as the in-plane lateral
acceleration `omega x R_hat`, which is the physically-correct PN turning force
and produces the lead/cut behaviour the DoD asks for.

Waypoint semantics (Q1, decided): PN steering "carrot" — rotate the velocity by
the commanded turn (clamped to max_turn_rate), then drop the waypoint
`speed * lookahead_s` ahead. Recomputed every update tick keeps reactivity.
"""

from __future__ import annotations

import numpy as np

Vec3 = tuple[float, float, float]

_EPS = 1e-9


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > _EPS else v


def _pt(v: np.ndarray) -> Vec3:
    return (float(v[0]), float(v[1]), float(v[2]))


def pn_acceleration(
    self_pos: Vec3,
    self_vel: Vec3,
    tgt_pos: Vec3,
    tgt_vel: Vec3,
    nav_constant: float,
    fallback_speed: float,
) -> np.ndarray:
    """PN lateral acceleration command (m/s^2), perpendicular to the LOS."""
    p = np.asarray(self_pos, dtype=float)
    v = np.asarray(self_vel, dtype=float)
    R = np.asarray(tgt_pos, dtype=float) - p  # relative position
    R_dot = np.asarray(tgt_vel, dtype=float) - v  # relative velocity
    rr = float(R @ R)
    if rr < _EPS:  # essentially on top of target
        return np.zeros(3)
    omega = np.cross(R, R_dot) / rr  # LOS rate (axis), rad/s
    speed = float(np.linalg.norm(v))
    if speed < _EPS:
        speed = fallback_speed
    return nav_constant * speed * np.cross(omega, _unit(R))


def _clamp_turn(v_from: np.ndarray, v_to: np.ndarray, max_angle: float) -> np.ndarray:
    """Rotate v_from toward v_to by at most max_angle (rad); keep |v_from|."""
    uf, ut = _unit(v_from), _unit(v_to)
    cos = float(np.clip(uf @ ut, -1.0, 1.0))
    ang = float(np.arccos(cos))
    if ang <= max_angle or ang < _EPS:
        return v_to
    axis = np.cross(uf, ut)
    if float(np.linalg.norm(axis)) < _EPS:  # parallel / antiparallel
        return v_to
    # slerp uf -> ut, stop at max_angle
    t = max_angle / ang
    dir_clamped = (np.sin((1.0 - t) * ang) * uf + np.sin(t * ang) * ut) / np.sin(ang)
    return np.asarray(dir_clamped * float(np.linalg.norm(v_from)), dtype=float)


def steering_waypoint(
    self_pos: Vec3,
    self_vel: Vec3,
    tgt_pos: Vec3,
    tgt_vel: Vec3,
    *,
    nav_constant: float,
    speed_mps: float,
    max_turn_rate_deg_s: float,
    dt: float,
    lookahead_s: float,
) -> Vec3:
    """PN carrot: the pursuit point to fly toward, `speed * lookahead_s` ahead."""
    p = np.asarray(self_pos, dtype=float)
    v = np.asarray(self_vel, dtype=float)

    if float(np.linalg.norm(v)) < _EPS:
        # No heading yet (just launched): aim straight at the target.
        direction = _unit(np.asarray(tgt_pos, dtype=float) - p)
        return _pt(p + direction * speed_mps * lookahead_s)

    a_cmd = pn_acceleration(self_pos, self_vel, tgt_pos, tgt_vel, nav_constant, speed_mps)
    v_des = v + a_cmd * dt
    v_des = _clamp_turn(v, v_des, np.radians(max_turn_rate_deg_s) * dt)
    direction = _unit(v_des)
    return _pt(p + direction * speed_mps * lookahead_s)
