"""End-to-end: Stone-Soup radar -> tracker -> threat scorer -> Hungarian optimizer.

A single integration run through the whole GS pipeline (review: "no end-to-end
scenario test"). It exercises the real wiring — fleet shape, optimizer over scored
tracks — that the per-unit tests don't see together.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from contracts.bus import MockBroker
from gs.fleet import FleetUnit, InterceptorFleet, Status
from gs.optimizer import AssignmentOptimizer
from gs.threat_assessor import ThreatAssessor
from gs.tracker import MultiTargetTracker
from sim.radar_stonesoup import StoneSoupRadar, TargetInit

T0 = datetime(2026, 6, 13, 12, 0, 0)


def _unit(iid: str, pos) -> FleetUnit:
    return FleetUnit(
        iid,
        pos,
        (0.0, 0.0, 0.0),
        speed_mps=300.0,
        range_m=1_000_000.0,
        status=Status.READY,
        assigned_track_id=None,
        alive=True,
        last_update=0.0,
    )


def test_radar_to_assignment_end_to_end() -> None:
    broker = MockBroker()
    radar = StoneSoupRadar(
        broker.endpoint("radar"),
        "radar",
        [
            TargetInit("a", (-3000.0, 50.0, 0.0, 0.0, 100.0, 0.0)),
            TargetInit("b", (0.0, 0.0, -3000.0, 50.0, 120.0, 0.0)),
        ],
        start_time=T0,
        seed=1,
        prob_detect=1.0,
    )
    tracker = MultiTargetTracker(start_time=T0)
    scorer = ThreatAssessor((0.0, 0.0, 0.0))  # defended asset at origin
    optimizer = AssignmentOptimizer()
    fleet = InterceptorFleet([_unit("i1", (-1500.0, 0.0, 0.0)), _unit("i2", (1500.0, 0.0, 0.0))])

    tracks: list = []
    result = None
    for k in range(1, 13):
        detections = radar.scan(T0 + timedelta(seconds=k))
        tracks = tracker.process(detections, float(k))
        threats = [scorer.assess(t) for t in tracks]
        result = optimizer.assign(threats, fleet.available(), float(k))

    # the two inbound drones survive the whole chain as two assignments
    assert len(tracks) == 2
    assert result is not None
    assert len(result.assignments) == 2
    assert result.held_interceptors == [] and result.uncovered_threats == []
    assert {a.interceptor_id for a in result.assignments} == {"i1", "i2"}
    # every assignment points at a real fused track and carries a lead waypoint
    track_ids = {t.track_id for t in tracks}
    for a in result.assignments:
        assert a.track_id in track_ids
        assert len(a.initial_waypoint) == 3
