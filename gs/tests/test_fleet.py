"""Unit tests for the GS interceptor fleet-state manager (FLEET_STATE_PLAN.md)."""

from __future__ import annotations

import math

from contracts.config import (
    InterceptorConfig,
    RadarConfig,
    ScenarioConfig,
    ShahedConfig,
)
from contracts.messages import EngagementEvent, InterceptorState
from gs.fleet import Interceptor, InterceptorFleet, Status


def _cfg(count: int = 4, launch=(0.0, 0.0, 0.0), speed=300.0, rng=8000.0) -> ScenarioConfig:
    return ScenarioConfig(
        seed=0,
        target_position=(0.0, 0.0, 0.0),
        duration_max=120.0,
        situation="B",
        radars=[RadarConfig(position=(0.0, 0.0, 0.0), range=10000.0, fov_deg=360.0, noise_std=5.0)],
        shaheds=ShahedConfig(
            count=1, speed_mps=(40.0, 60.0), spawn_radius=3000.0, spawn_angle_spread_deg=360.0
        ),
        interceptors=InterceptorConfig(
            count=count,
            speed_mps=speed,
            max_turn_rate_deg_s=30.0,
            range_m=rng,
            launch_position=launch,
        ),
    )


def _state(iid, pos, vel=(0.0, 0.0, 0.0), track="T1", alive=True, t=1.0) -> InterceptorState:
    return InterceptorState(
        interceptor_id=iid,
        position=pos,
        velocity=vel,
        assigned_track_id=track,
        alive=alive,
        timestamp=t,
    )


# 1 — init: N READY units at N distinct ring positions, each at ring_radius.
def test_init_places_units_on_distinct_ring() -> None:
    fleet = InterceptorFleet.from_config(_cfg(count=4), ring_radius=300.0)
    snap = fleet.snapshot()
    assert len(snap) == 4
    assert all(u.status is Status.READY for u in snap)
    positions = [u.position for u in snap]
    assert len({(round(x, 3), round(y, 3)) for x, y, _ in positions}) == 4  # distinct
    for x, y, _ in positions:
        assert math.isclose(math.hypot(x, y), 300.0, abs_tol=1e-6)  # on the ring


# 2 — explicit positions override the ring verbatim.
def test_explicit_positions_override_ring() -> None:
    sites = [(100.0, 0.0, 0.0), (-100.0, 0.0, 0.0)]
    fleet = InterceptorFleet.from_config(_cfg(count=2), positions=sites)
    assert [u.position for u in fleet.snapshot()] == sites


def test_explicit_positions_count_mismatch_raises() -> None:
    try:
        InterceptorFleet.from_config(_cfg(count=3), positions=[(0.0, 0.0, 0.0)])
    except ValueError:
        return
    raise AssertionError("expected ValueError on count mismatch")


# 3 — available() filters by status; mark_assigned shrinks the pool.
def test_available_filters_and_assignment_shrinks_pool() -> None:
    fleet = InterceptorFleet.from_config(_cfg(count=3))
    assert len(fleet.available()) == 3
    fleet.mark_assigned("i1", "T7")
    avail = fleet.available()
    assert len(avail) == 2
    assert "i1" not in {a.interceptor_id for a in avail}
    assert fleet.get("i1").status is Status.ASSIGNED
    assert fleet.get("i1").assigned_track_id == "T7"


# 4 — assign -> launch: first state broadcast promotes ASSIGNED -> IN_FLIGHT.
def test_assigned_unit_goes_in_flight_on_first_state() -> None:
    fleet = InterceptorFleet.from_config(_cfg(count=2))
    fleet.mark_assigned("i1", "T1")
    fleet.on_interceptor_state(_state("i1", (10.0, 20.0, 100.0), vel=(5.0, 0.0, 0.0)))
    u = fleet.get("i1")
    assert u.status is Status.IN_FLIGHT
    assert u.position == (10.0, 20.0, 100.0)
    assert u.velocity == (5.0, 0.0, 0.0)
    assert "i1" not in {a.interceptor_id for a in fleet.available()}


# 5 — engagement is terminal; no resurrection from a later state.
def test_engagement_is_terminal() -> None:
    fleet = InterceptorFleet.from_config(_cfg(count=1))
    fleet.mark_assigned("i1", "T1")
    fleet.on_interceptor_state(_state("i1", (50.0, 0.0, 100.0)))
    fleet.on_engagement(
        EngagementEvent("i1", "T1", success=True, position=(60.0, 0.0, 100.0), timestamp=5.0)
    )
    assert fleet.get("i1").status is Status.EXPENDED
    assert fleet.available() == []
    # a stray later broadcast must not revive it
    fleet.on_interceptor_state(_state("i1", (70.0, 0.0, 100.0), t=6.0))
    assert fleet.get("i1").status is Status.EXPENDED


# 6 — loss: alive=False -> DOWN, not available.
def test_dead_interceptor_goes_down() -> None:
    fleet = InterceptorFleet.from_config(_cfg(count=2))
    fleet.on_interceptor_state(_state("i1", (10.0, 0.0, 50.0), alive=False))
    assert fleet.get("i1").status is Status.DOWN
    assert "i1" not in {a.interceptor_id for a in fleet.available()}


# 7 — unknown id is ignored, no error, fleet unchanged.
def test_unknown_id_is_ignored() -> None:
    fleet = InterceptorFleet.from_config(_cfg(count=2))
    before = fleet.counts()
    fleet.on_interceptor_state(_state("ghost", (0.0, 0.0, 0.0)))
    fleet.on_engagement(EngagementEvent("ghost", "T1", True, (0.0, 0.0, 0.0), 1.0))
    fleet.mark_assigned("ghost", "T1")
    assert fleet.counts() == before
    assert fleet.get("ghost") is None


# 8 — counts() reflects the status distribution through a lifecycle.
def test_counts_track_the_lifecycle() -> None:
    fleet = InterceptorFleet.from_config(_cfg(count=4))
    assert fleet.counts()[Status.READY] == 4
    fleet.mark_assigned("i1", "T1")
    fleet.on_interceptor_state(_state("i1", (1.0, 1.0, 1.0)))  # -> IN_FLIGHT
    fleet.on_engagement(EngagementEvent("i2", "T2", False, (0.0, 0.0, 0.0), 2.0))  # -> EXPENDED
    fleet.on_interceptor_state(_state("i3", (0.0, 0.0, 0.0), alive=False))  # -> DOWN
    c = fleet.counts()
    assert c[Status.READY] == 1
    assert c[Status.IN_FLIGHT] == 1
    assert c[Status.EXPENDED] == 1
    assert c[Status.DOWN] == 1


# 9 — available() yields the optimizer's Interceptor shape.
def test_available_matches_optimizer_interceptor_shape() -> None:
    fleet = InterceptorFleet.from_config(_cfg(count=2, speed=320.0, rng=7000.0))
    a = fleet.available()[0]
    assert isinstance(a, Interceptor)
    assert a.speed_mps == 320.0
    assert a.range_m == 7000.0
    assert len(a.position) == 3
