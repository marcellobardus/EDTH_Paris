"""Integration tests for the assignment publisher (HUNGARIAN_OPTIMIZER_PLAN Phase 2)."""

from __future__ import annotations

from agent.bus import MockBroker
from contracts.messages import Assignment, ThreatAssessment
from contracts.topics import Topics
from gs.assignment_publisher import AssignmentPublisher
from gs.fleet import FleetUnit, InterceptorFleet, Status


def _fleet(positions) -> InterceptorFleet:
    units = [
        FleetUnit(
            interceptor_id=f"i{i + 1}",
            position=p,
            velocity=(0.0, 0.0, 0.0),
            speed_mps=300.0,
            range_m=1_000_000.0,
            status=Status.READY,
            assigned_track_id=None,
            alive=True,
            last_update=0.0,
        )
        for i, p in enumerate(positions)
    ]
    return InterceptorFleet(units)


def _threat(tid, pos, score=1.0, eta=100.0, t=1.0) -> ThreatAssessment:
    return ThreatAssessment(
        track_id=tid,
        position=pos,
        velocity=(0.0, 0.0, 0.0),
        threat_score=score,
        eta_seconds=eta,
        timestamp=t,
    )


def _wire(broker: MockBroker, fleet: InterceptorFleet, **kw):
    sink: list[Assignment] = []
    broker.endpoint("sink").subscribe(Topics.GS_ASSIGNMENTS, Assignment, sink.append)
    pub = AssignmentPublisher(broker.endpoint("gs"), fleet, **kw)
    producer = broker.endpoint("threats")
    return pub, producer, sink


def test_threats_in_produce_assignments_out() -> None:
    broker = MockBroker()
    fleet = _fleet([(-2000.0, 0.0, 0.0), (2000.0, 0.0, 0.0)])
    pub, producer, sink = _wire(broker, fleet)

    producer.publish(Topics.GS_THREATS, _threat("Tl", (-3000.0, 0.0, 100.0)))
    producer.publish(Topics.GS_THREATS, _threat("Tr", (3000.0, 0.0, 100.0)))
    result = pub.assign_now()

    assert {a.interceptor_id: a.track_id for a in sink} == {"i1": "Tl", "i2": "Tr"}
    assert len(result.assignments) == 2
    assert result.held_interceptors == [] and result.uncovered_threats == []
    # initial_waypoint is populated (the lead point)
    assert all(len(a.initial_waypoint) == 3 for a in sink)


def test_commit_marks_units_assigned_and_shrinks_pool() -> None:
    broker = MockBroker()
    fleet = _fleet([(-2000.0, 0.0, 0.0), (2000.0, 0.0, 0.0)])
    pub, producer, _ = _wire(broker, fleet)
    producer.publish(Topics.GS_THREATS, _threat("Tl", (-3000.0, 0.0, 100.0)))

    pub.assign_now(commit=True)
    assert fleet.counts()[Status.ASSIGNED] == 1
    assert len(fleet.available()) == 1  # one unit committed, pool shrank
    # the other threat side has no unit assigned yet
    assert fleet.get("i1").assigned_track_id == "Tl"


def test_non_commit_leaves_pool_full_for_live_retasking() -> None:
    broker = MockBroker()
    fleet = _fleet([(-2000.0, 0.0, 0.0), (2000.0, 0.0, 0.0)])
    pub, producer, _ = _wire(broker, fleet)
    producer.publish(Topics.GS_THREATS, _threat("Tl", (-3000.0, 0.0, 100.0)))

    pub.assign_now(commit=False)
    pub.assign_now(commit=False)  # re-solve repeatedly
    assert len(fleet.available()) == 2  # nothing committed; full pool


def test_stale_threats_expire_from_the_snapshot() -> None:
    broker = MockBroker()
    fleet = _fleet([(0.0, 0.0, 0.0)])
    pub, producer, _ = _wire(broker, fleet, expiry=3.0)

    producer.publish(Topics.GS_THREATS, _threat("old", (1000.0, 0.0, 100.0), t=1.0))
    # a newer threat advances the clock well past the old one's expiry window
    producer.publish(Topics.GS_THREATS, _threat("new", (500.0, 0.0, 100.0), t=10.0))
    result = pub.assign_now()

    covered = {a.track_id for a in result.assignments}
    assert "new" in covered
    assert "old" not in covered  # expired out of the snapshot
