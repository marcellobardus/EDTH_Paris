"""Headless tests for the sim driver's pure kinematics (no rclpy needed)."""

from __future__ import annotations

import math

from contracts.config import (
    InterceptorConfig,
    RadarConfig,
    ScenarioConfig,
    ScenarioMeta,
    ShahedConfig,
)
from sim.driver import KILL_RADIUS_M, Interceptor, Shahed, SimWorld


def _cfg(n_interceptors: int = 2, n_shaheds: int = 2) -> ScenarioConfig:
    return ScenarioConfig(
        scenario=ScenarioMeta(
            seed=42, target_position=(500.0, 500.0, 0.0), duration_max=120.0, situation="B"
        ),
        radars=[
            RadarConfig(position=(100.0, 100.0, 10.0), range=800.0, fov_deg=360.0, noise_std=5.0)
        ],
        shaheds=ShahedConfig(
            count=n_shaheds,
            speed_mps=(15.0, 25.0),
            spawn_radius=1000.0,
            spawn_angle_spread_deg=360.0,
        ),
        interceptors=InterceptorConfig(
            count=n_interceptors,
            speed_mps=40.0,
            max_turn_rate_deg_s=30.0,
            range_m=700.0,
            launch_position=(480.0, 480.0, 0.0),
        ),
    )


def test_interceptor_homes_toward_waypoint() -> None:
    # Interceptors are flown by gz VelocityControl: the driver pushes
    # desired_world_velocity() out as cmd_vel and physics flies the body there.
    itc = Interceptor("i1", (0.0, 0.0, 0.0), speed=40.0, max_turn_rate_deg_s=30.0)
    itc.waypoint = (1000.0, 0.0, 0.0)
    v = itc.desired_world_velocity()
    # Points straight at the waypoint (+x) at cruise speed.
    assert v[0] > 0 and abs(v[1]) < 1e-6 and abs(v[2]) < 1e-6
    assert math.isclose(math.sqrt(sum(c * c for c in v)), 40.0, rel_tol=1e-6)


def test_shahed_reaches_target_and_leaks() -> None:
    # gz flies the shahed (its pose is refreshed from the world); check_reached()
    # only watches for arrival at the defended target.
    sh = Shahed("t1", (560.0, 500.0, 0.0), speed=20.0, target=(500.0, 500.0, 0.0))
    sh.check_reached()
    assert sh.alive and not sh.reached  # still inbound
    sh.pos = (500.5, 500.0, 0.0)  # gz has flown it onto the target
    sh.check_reached()
    assert not sh.alive and sh.reached


def test_engagement_kills_and_expends() -> None:
    world = SimWorld(_cfg(), seed=1)
    itc = world.interceptors[0]
    target = world.shaheds[0]
    itc.pos = target.pos  # place the interceptor on top of the threat
    itc.waypoint = None  # hold position
    result = world.step(1.0 / 50.0)
    assert len(result.engagements) == 1
    ev = result.engagements[0]
    assert ev.interceptor_id == itc.id and ev.track_id == target.id and ev.success
    assert not target.alive  # threat neutralised
    assert not itc.alive  # one interceptor, one shot


def test_far_interceptor_does_not_engage() -> None:
    world = SimWorld(_cfg(), seed=1)
    world.interceptors[0].pos = (0.0, 0.0, 0.0)  # nowhere near any threat
    world.interceptors[1].pos = (0.0, 0.0, 0.0)
    result = world.step(1.0 / 50.0)
    assert result.engagements == []
    assert all(itc.alive for itc in world.interceptors)


def test_id_conventions_are_consistent() -> None:
    world = SimWorld(_cfg(n_interceptors=3, n_shaheds=4), seed=7)
    gt = world.ground_truth()
    interceptors = {o.object_id for o in gt if o.kind == "interceptor"}
    shaheds = {o.object_id for o in gt if o.kind == "shahed"}
    assert interceptors == {"i1", "i2", "i3"}
    assert shaheds == {"t1", "t2", "t3", "t4"}
    # track_id == shahed ground-truth id (the track *is* the Shahed here).
    assert {t.track_id for t in world.tracks()} == shaheds
    # Every assignment targets a real track and a real interceptor.
    for a in world.initial_assignments():
        assert a.interceptor_id in interceptors and a.track_id in shaheds


def test_kill_radius_default_sane() -> None:
    assert 5.0 <= KILL_RADIUS_M <= 50.0
