"""Unit tests for the Hungarian assignment optimizer (HUNGARIAN_OPTIMIZER_PLAN.md)."""

from __future__ import annotations

import math

from contracts.messages import ThreatAssessment
from gs.fleet import Interceptor
from gs.optimizer import AssignmentOptimizer, intercept


def _threat(tid, pos, vel=(0.0, 0.0, 0.0), score=1.0, eta=100.0, t=0.0) -> ThreatAssessment:
    return ThreatAssessment(
        track_id=tid, position=pos, velocity=vel, threat_score=score, eta_seconds=eta, timestamp=t
    )


def _intc(iid, pos, speed=300.0, rng=1_000_000.0) -> Interceptor:
    return Interceptor(interceptor_id=iid, position=pos, speed_mps=speed, range_m=rng)


def _pairs(result):
    return {a.interceptor_id: a.track_id for a in result.assignments}


# 1 — 2×2: each interceptor takes the threat on its side.
def test_two_by_two_each_takes_its_side() -> None:
    opt = AssignmentOptimizer()
    interceptors = [_intc("i1", (-2000.0, 0.0, 0.0)), _intc("i2", (2000.0, 0.0, 0.0))]
    threats = [_threat("Tl", (-3000.0, 0.0, 100.0)), _threat("Tr", (3000.0, 0.0, 100.0))]
    res = opt.assign(threats, interceptors, 0.0)
    assert _pairs(res) == {"i1": "Tl", "i2": "Tr"}
    assert res.held_interceptors == [] and res.uncovered_threats == []


# 2 — under scarcity, the single interceptor covers the higher-threat target.
def test_scarcity_prefers_higher_threat() -> None:
    opt = AssignmentOptimizer()
    interceptors = [_intc("i1", (0.0, 0.0, 0.0))]
    threats = [
        _threat("low", (0.0, 3000.0, 100.0), score=0.02),
        _threat("high", (3000.0, 0.0, 100.0), score=0.20),  # same range, 10× threat
    ]
    res = opt.assign(threats, interceptors, 0.0)
    assert _pairs(res) == {"i1": "high"}
    assert res.uncovered_threats == ["low"]


# 3 — Hungarian beats nearest-first greedy on a crafted trap.
def test_beats_greedy_trap() -> None:
    # Costs (stationary, score 1, speed 300 ⇒ cost = distance/300):
    #         T1     T2
    #   i1   1.21   2.36
    #   i2   0.37   1.37
    # Greedy grabs the global-cheapest cell i2-T1 (0.37) → forces i1-T2 → 2.73.
    # Optimal swaps to i1-T1 + i2-T2 = 2.58.
    opt = AssignmentOptimizer()
    interceptors = [_intc("i1", (0.0, 0.0, 0.0)), _intc("i2", (300.0, 0.0, 0.0))]
    threats = [_threat("T1", (350.0, 0.0, 100.0)), _threat("T2", (700.0, 0.0, 100.0))]
    res = opt.assign(threats, interceptors, 0.0)
    assert _pairs(res) == {"i1": "T1", "i2": "T2"}  # the optimum greedy misses


# 4 — a threat beyond every interceptor's range is uncovered (FR-5.2).
def test_out_of_range_is_uncovered() -> None:
    opt = AssignmentOptimizer()
    interceptors = [_intc("i1", (0.0, 0.0, 0.0), rng=500.0)]
    threats = [_threat("far", (5000.0, 0.0, 100.0))]
    res = opt.assign(threats, interceptors, 0.0)
    assert res.assignments == []
    assert res.held_interceptors == ["i1"]
    assert res.uncovered_threats == ["far"]


# 5 — a target outrunning the interceptor cannot be intercepted.
def test_uncatchable_target_is_infeasible() -> None:
    fast_away = _threat("runner", (1000.0, 0.0, 0.0), vel=(400.0, 0.0, 0.0))
    assert intercept(_intc("i1", (0.0, 0.0, 0.0), speed=300.0), fast_away) is None
    res = AssignmentOptimizer().assign([fast_away], [_intc("i1", (0.0, 0.0, 0.0))], 0.0)
    assert res.uncovered_threats == ["runner"]


# 6 — the lead point is correct: stationary exact, moving target reachable in t.
def test_lead_point_correct() -> None:
    # stationary: intercept point == position, t == distance / speed
    stat = _threat("s", (3000.0, 0.0, 100.0))
    t, point = intercept(_intc("i1", (0.0, 0.0, 0.0), speed=300.0), stat)
    assert point == (3000.0, 0.0, 100.0)
    assert math.isclose(t, math.dist((0.0, 0.0, 0.0), (3000.0, 0.0, 100.0)) / 300.0, rel_tol=1e-6)

    # crossing target: the interceptor really reaches the lead point in time t
    cross = _threat("c", (0.0, 3000.0, 100.0), vel=(0.0, -50.0, 0.0))
    t2, lead = intercept(_intc("i1", (0.0, 0.0, 0.0), speed=300.0), cross)
    assert math.isclose(math.dist((0.0, 0.0, 0.0), lead), 300.0 * t2, rel_tol=1e-6)


# 7 — rectangular and empty inputs: surplus holds / all uncovered, never raises.
def test_rectangular_and_empty() -> None:
    opt = AssignmentOptimizer()
    interceptors = [
        _intc("i1", (-2000.0, 0.0, 0.0)),
        _intc("i2", (2000.0, 0.0, 0.0)),
        _intc("i3", (0.0, 2000.0, 0.0)),
    ]
    threats = [_threat("Tl", (-2500.0, 0.0, 100.0)), _threat("Tr", (2500.0, 0.0, 100.0))]
    res = opt.assign(threats, interceptors, 0.0)
    assert len(res.assignments) == 2
    assert len(res.held_interceptors) == 1  # one surplus interceptor holds

    assert opt.assign([], interceptors, 0.0).held_interceptors == ["i1", "i2", "i3"]
    assert opt.assign(threats, [], 0.0).uncovered_threats == ["Tl", "Tr"]


# 8 — the beat-eta gate rejects intercepts that land after impact.
def test_beat_eta_gate() -> None:
    interceptors = [_intc("i1", (0.0, 0.0, 0.0), speed=300.0)]
    # ~10 s to reach, but the threat impacts in 5 s.
    threats = [_threat("imminent", (3000.0, 0.0, 100.0), eta=5.0)]
    gated = AssignmentOptimizer(require_beat_eta=True).assign(threats, interceptors, 0.0)
    ungated = AssignmentOptimizer(require_beat_eta=False).assign(threats, interceptors, 0.0)
    assert gated.assignments == []
    assert ungated.assignments != []


# 9 — distinct positions route each threat to its nearer interceptor; the
#     co-located fleet still produces a valid (if arbitrary) covering assignment.
def test_distinct_positions_route_to_nearer() -> None:
    opt = AssignmentOptimizer()
    spread = [_intc("i1", (-2000.0, 0.0, 0.0)), _intc("i2", (2000.0, 0.0, 0.0))]
    threats = [_threat("Tl", (-3000.0, 0.0, 100.0)), _threat("Tr", (3000.0, 0.0, 100.0))]
    assert _pairs(opt.assign(threats, spread, 0.0)) == {"i1": "Tl", "i2": "Tr"}

    colocated = [_intc("i1", (0.0, 0.0, 0.0)), _intc("i2", (0.0, 0.0, 0.0))]
    res = opt.assign(threats, colocated, 0.0)
    assert len(res.assignments) == 2  # both threats still covered
    assert {a.track_id for a in res.assignments} == {"Tl", "Tr"}
