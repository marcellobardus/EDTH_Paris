"""
Hungarian assignment optimizer (milestone G4).

Pairs each available interceptor to at most one threat so the fleet neutralises
the most dangerous targets soonest. Builds the architecture cost matrix

    C[i][j] = intercept_time[i][j] / threat_score[j]   if feasible
            = 1e9 (sentinel)                            otherwise

and solves it optimally with ``scipy.optimize.linear_sum_assignment``. Infeasible
picks are stripped after the solve (those interceptors hold).

The interceptor pool comes from :class:`gs.fleet.InterceptorFleet.available` — the
``Interceptor`` type is re-exported here so callers can use ``optimizer.Interceptor``.
``intercept_time`` is the lead-pursuit solution (the smallest time at which an
interceptor leaving its site at speed ``s`` can meet a threat moving at ``v``),
which yields the ``Assignment.initial_waypoint`` for free.

Pure and deterministic: no bus, no clock. See ``HUNGARIAN_OPTIMIZER_PLAN.md``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from contracts.messages import Assignment, ThreatAssessment
from scipy.optimize import linear_sum_assignment

from gs.fleet import Interceptor  # re-exported: this is the optimizer's input shape

__all__ = ["Interceptor", "AssignmentOptimizer", "AssignmentResult", "intercept", "INFEASIBLE"]

Vec3 = tuple[float, float, float]
INFEASIBLE = 1e9
log = logging.getLogger("gs.optimizer")


@dataclass(frozen=True)
class AssignmentResult:
    assignments: list[Assignment]  # one per committed interceptor
    held_interceptors: list[str]  # FR-5.4: no feasible / needed target
    uncovered_threats: list[str]  # FR-5.4: no interceptor assigned


def _smallest_positive_root(a: float, b: float, c: float) -> float | None:
    """Smallest t > 0 solving a·t² + b·t + c = 0, or None if there is none."""
    if abs(a) < 1e-9:  # degenerate: linear  b·t + c = 0
        if abs(b) < 1e-12:
            return None
        t = -c / b
        return t if t > 1e-9 else None
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return None  # never catches it
    sq = math.sqrt(disc)
    roots = ((-b - sq) / (2.0 * a), (-b + sq) / (2.0 * a))
    positives = [t for t in roots if t > 1e-9]
    return min(positives) if positives else None


def intercept(interceptor: Interceptor, threat: ThreatAssessment) -> tuple[float, Vec3] | None:
    """Smallest positive intercept time + the lead point, or None if uncatchable.

    Solves |Δ + v·t| = s·t for t, where Δ = threat − interceptor.
    """
    lx, ly, lz = interceptor.position
    px, py, pz = threat.position
    vx, vy, vz = threat.velocity
    s = interceptor.speed_mps
    dx, dy, dz = px - lx, py - ly, pz - lz

    a = (vx * vx + vy * vy + vz * vz) - s * s
    b = 2.0 * (dx * vx + dy * vy + dz * vz)
    c = dx * dx + dy * dy + dz * dz
    t = _smallest_positive_root(a, b, c)
    if t is None:
        return None
    return t, (px + vx * t, py + vy * t, pz + vz * t)


class AssignmentOptimizer:
    """Optimal interceptor→threat assignment over the architecture cost matrix.

    ``require_beat_eta`` also rejects intercepts that land *after* the threat's
    ``eta_seconds`` (no point hitting it after impact). Default on.
    """

    def __init__(self, *, require_beat_eta: bool = True) -> None:
        self._beat_eta = require_beat_eta

    def assign(
        self,
        threats: list[ThreatAssessment],
        interceptors: list[Interceptor],
        timestamp: float,
    ) -> AssignmentResult:
        # Deterministic ordering so ties resolve stably across runs.
        interceptors = sorted(interceptors, key=lambda i: i.interceptor_id)
        threats = sorted(threats, key=lambda t: t.track_id)
        n, m = len(interceptors), len(threats)

        if n == 0:
            return AssignmentResult([], [], [t.track_id for t in threats])
        if m == 0:
            return AssignmentResult([], [i.interceptor_id for i in interceptors], [])

        cost = np.full((n, m), INFEASIBLE)
        points: list[list[Vec3 | None]] = [[None] * m for _ in range(n)]
        for i, unit in enumerate(interceptors):
            for j, threat in enumerate(threats):
                sol = intercept(unit, threat)
                if sol is None:
                    continue  # uncatchable
                t, point = sol
                if unit.speed_mps * t > unit.range_m:
                    continue  # out of range (FR-5.2)
                if self._beat_eta and t >= threat.eta_seconds:
                    continue  # reached after impact
                score = threat.threat_score
                if score <= 0.0:
                    log.warning(
                        "non-positive threat_score %.3g for %s; clamping", score, threat.track_id
                    )
                    score = 1e-6
                cost[i, j] = t / score
                points[i][j] = point

        rows, cols = linear_sum_assignment(cost)
        assignments: list[Assignment] = []
        committed_rows: set[int] = set()
        committed_cols: set[int] = set()
        for i, j in zip(rows, cols):
            if cost[i, j] >= INFEASIBLE:
                continue  # infeasible pick → hold
            assignments.append(
                Assignment(
                    interceptor_id=interceptors[i].interceptor_id,
                    track_id=threats[j].track_id,
                    initial_waypoint=points[i][j],
                    timestamp=timestamp,
                )
            )
            committed_rows.add(i)
            committed_cols.add(j)

        held = [interceptors[i].interceptor_id for i in range(n) if i not in committed_rows]
        uncovered = [threats[j].track_id for j in range(m) if j not in committed_cols]
        return AssignmentResult(assignments, held, uncovered)
