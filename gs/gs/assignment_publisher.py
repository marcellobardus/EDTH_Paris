"""
Assignment publisher — bus bridge for the Hungarian optimizer (G4 Phase 2).

``/gs/threats`` is a *stream*: one ``ThreatAssessment`` per track per scan. The
optimizer needs a coherent **snapshot** of all current threats at the moment it
solves. This class buffers the latest assessment per ``track_id`` (expiring stale
ones), and on :meth:`assign_now` snapshots them, asks the
:class:`~gs.fleet.InterceptorFleet` for its available units, runs the optimizer,
publishes one ``Assignment`` per committed pair on ``/gs/assignments``, and
(optionally) commits the units back to the fleet.

Two solve modes:
- **live re-tasking** (``commit=False``) — re-solve each tick over the full READY
  pool; assignments track the evolving threat picture. Good for the viewer.
- **one-shot launch** (``commit=True``) — mark the chosen units ASSIGNED so they
  leave the pool. The faithful pre-launch burst (GS role ends at launch).

Bus-light: holds only the ``Bus`` + fleet + optimizer, so tests drive it with a
``MockBroker``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from contracts.bus import Bus
from contracts.messages import ThreatAssessment
from contracts.topics import Topics

from gs.fleet import InterceptorFleet
from gs.optimizer import AssignmentOptimizer, AssignmentResult

log = logging.getLogger("gs.assignment")


class AssignmentPublisher:
    """Buffers the threat stream and publishes optimal assignments on demand."""

    def __init__(
        self,
        bus: Bus,
        fleet: InterceptorFleet,
        *,
        optimizer: AssignmentOptimizer | None = None,
        on_assignments: Callable[[AssignmentResult, float], None] | None = None,
        expiry: float = 3.0,
    ) -> None:
        self._bus = bus
        self._fleet = fleet
        self._optimizer = optimizer or AssignmentOptimizer()
        self._on_assignments = on_assignments
        self._expiry = expiry
        self._threats: dict[str, ThreatAssessment] = {}  # track_id -> latest
        self._clock = 0.0  # latest threat timestamp seen
        bus.subscribe(Topics.GS_THREATS, ThreatAssessment, self._on_threat)

    def _on_threat(self, threat: ThreatAssessment) -> None:
        self._threats[threat.track_id] = threat
        self._clock = max(self._clock, threat.timestamp)

    def _current_threats(self) -> list[ThreatAssessment]:
        """Drop assessments the GS has stopped publishing (track dropped), then
        return the live snapshot."""
        for tid in [
            t for t, th in self._threats.items() if self._clock - th.timestamp > self._expiry
        ]:
            del self._threats[tid]
        return list(self._threats.values())

    def assign_now(
        self, *, commit: bool = False, timestamp: float | None = None
    ) -> AssignmentResult:
        """Solve over the current threat snapshot + available fleet and publish
        the assignments. With ``commit`` the chosen units are marked ASSIGNED."""
        threats = self._current_threats()
        ts = self._clock if timestamp is None else timestamp
        result = self._optimizer.assign(threats, self._fleet.available(), ts)
        for assignment in result.assignments:
            self._bus.publish(Topics.GS_ASSIGNMENTS, assignment)
            if commit:
                self._fleet.mark_assigned(assignment.interceptor_id, assignment.track_id)
        if self._on_assignments is not None:
            self._on_assignments(result, ts)
        return result
