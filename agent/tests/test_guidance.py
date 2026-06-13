"""A2 guidance tests (FR-6 DoD): straight-on, crossing lead, and just-launched."""

import numpy as np
from agent.guidance import pn_acceleration, steering_waypoint

GUIDANCE = dict(
    nav_constant=4.0,
    speed_mps=40.0,
    max_turn_rate_deg_s=30.0,
    dt=0.1,
    lookahead_s=1.0,
)


def test_pure_closing_gives_no_lateral_command() -> None:
    # Interceptor heading straight at a stationary target -> zero LOS rate.
    self_pos = (0.0, 0.0, 0.0)
    self_vel = (40.0, 0.0, 0.0)  # flying +x, target is on +x
    tgt_pos = (100.0, 0.0, 0.0)
    tgt_vel = (0.0, 0.0, 0.0)
    a = pn_acceleration(self_pos, self_vel, tgt_pos, tgt_vel, 4.0, 40.0)
    assert float(np.linalg.norm(a)) < 1e-6


def test_crossing_target_produces_lead() -> None:
    # Target crosses left->right (+y); PN must turn toward where it's going,
    # i.e. the waypoint leads the target's current position in +y.
    self_pos = (0.0, 0.0, 0.0)
    self_vel = (40.0, 0.0, 0.0)
    tgt_pos = (100.0, 0.0, 0.0)
    tgt_vel = (0.0, 20.0, 0.0)
    a = pn_acceleration(self_pos, self_vel, tgt_pos, tgt_vel, 4.0, 40.0)
    assert float(np.linalg.norm(a)) > 1e-3
    assert a[1] > 0.0  # lateral command points toward target motion

    wp = steering_waypoint(self_pos, self_vel, tgt_pos, tgt_vel, **GUIDANCE)
    # Pure pursuit would keep y ~ 0; PN leads, so the waypoint bends +y.
    assert wp[1] > 0.0


def test_turn_rate_is_clamped() -> None:
    # Target directly behind -> PN wants a huge turn; one tick may not exceed
    # max_turn_rate_deg_s * dt. Heading change is bounded by that limit.
    self_pos = (0.0, 0.0, 0.0)
    self_vel = (40.0, 0.0, 0.0)
    tgt_pos = (-100.0, 5.0, 0.0)
    tgt_vel = (0.0, 0.0, 0.0)
    wp = steering_waypoint(self_pos, self_vel, tgt_pos, tgt_vel, **GUIDANCE)
    heading = np.arctan2(wp[1], wp[0])  # waypoint direction from origin
    max_step = np.radians(GUIDANCE["max_turn_rate_deg_s"] * GUIDANCE["dt"])
    assert abs(heading) <= max_step + 1e-6


def test_just_launched_aims_at_target() -> None:
    # Zero velocity (no heading): waypoint points straight at the target.
    self_pos = (0.0, 0.0, 0.0)
    self_vel = (0.0, 0.0, 0.0)
    tgt_pos = (0.0, 100.0, 0.0)
    wp = steering_waypoint(self_pos, self_vel, tgt_pos, (0.0, 0.0, 0.0), **GUIDANCE)
    assert wp[0] == 0.0 and wp[1] > 0.0  # along +y, toward the target
