"""
Assignment publisher — bus bridge + solve cadence for the Hungarian optimizer
(G4 Phase 2).

``/gs/threats`` is a *stream*: one ``ThreatAssessment`` per track per scan. The
optimizer needs a coherent **snapshot** of all current threats at the moment it
solves. This class buffers the latest assessment per ``track_id`` (expiring stale
ones) and turns that stream into ``/gs/assignments``.

Two solve modes (FR-5 vs. demo):

- **oneshot** (default) — the faithful pre-launch burst: wait for the threat
  picture to stabilise (the go-signal), solve **once** with ``commit=True`` so the
  chosen units leave the READY pool, then *stop solving* and just republish the
  committed plan each tick (so the viewer stays fresh and late subscribers catch
  up). This is what FR-5 specifies.
- **continuous** — a live re-tasking view: re-solve each tick over the remaining
  available pool, committing as it goes, so the plan grows as new threats appear.
  Useful for the demo; not the pre-launch contract.

Either way the fleet **is** committed — the old behaviour (re-assigning the full
READY pool every tick, never committing) is gone.

Feed it threats from the bus (standalone ``assignment_node``) or directly via
:meth:`submit` (when the ground station runs the optimizer in-process). Bus-light:
holds only the ``Bus`` + fleet + optimizer, so tests drive it with a ``MockBroker``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from contracts.bus import Bus
from contracts.messages import Assignment, ThreatAssessment
from contracts.topics import Topics

from gs.fleet import InterceptorFleet
from gs.optimizer import AssignmentOptimizer, AssignmentResult

log = logging.getLogger("gs.assignment")


class AssignmentPublisher:
    """Buffers the threat stream and publishes committed assignments."""

    def __init__(
        self,
        bus: Bus,
        fleet: InterceptorFleet,
        *,
        optimizer: AssignmentOptimizer | None = None,
        on_assignments: Callable[[AssignmentResult, float], None] | None = None,
        expiry: float = 3.0,
        mode: str = "oneshot",
        stable_ticks: int = 3,
        subscribe_threats: bool = True,
    ) -> None:
        if mode not in ("oneshot", "continuous"):
            raise ValueError(f"mode must be 'oneshot' or 'continuous', got {mode!r}")
        self._bus = bus
        self._fleet = fleet
        self._optimizer = optimizer or AssignmentOptimizer()
        self._on_assignments = on_assignments
        self._expiry = expiry
        self._mode = mode
        self._stable_ticks = stable_ticks
        self._threats: dict[str, ThreatAssessment] = {}  # track_id -> latest
        self._clock = 0.0  # latest threat timestamp seen
        self._plan: list[Assignment] = []  # the standing committed plan
        self._fired = False  # oneshot: has the burst gone out?
        self._last_ids: frozenset[str] = frozenset()
        self._stable = 0
        if subscribe_threats:
            bus.subscribe(Topics.GS_THREATS, ThreatAssessment, self._on_threat)

    # -- threat intake -------------------------------------------------------

    def _on_threat(self, threat: ThreatAssessment) -> None:
        self._threats[threat.track_id] = threat
        self._clock = max(self._clock, threat.timestamp)

    def submit(self, threats: list[ThreatAssessment]) -> None:
        """Feed threats directly (in-process, no bus) — used when the GS runs the
        optimizer in the same process that scored them."""
        for threat in threats:
            self._on_threat(threat)

    def _current_threats(self) -> list[ThreatAssessment]:
        """Drop assessments the GS has stopped publishing (track dropped), then
        return the live snapshot."""
        for tid in [
            t for t, th in self._threats.items() if self._clock - th.timestamp > self._expiry
        ]:
            del self._threats[tid]
        return list(self._threats.values())

    # -- solving -------------------------------------------------------------

    def _solve(self, *, commit: bool, timestamp: float) -> AssignmentResult:
        """Solve over the current snapshot + available fleet (no publishing)."""
        result = self._optimizer.assign(self._current_threats(), self._fleet.available(), timestamp)
        if commit:
            for a in result.assignments:
                self._fleet.mark_assigned(a.interceptor_id, a.track_id)
        return result

    def _republish(self, timestamp: float) -> None:
        """Re-emit the standing plan (re-stamped) so the viewer stays fresh."""
        for a in self._plan:
            self._bus.publish(Topics.GS_ASSIGNMENTS, replace(a, timestamp=timestamp))

    def assign_now(
        self, *, commit: bool = False, timestamp: float | None = None
    ) -> AssignmentResult:
        """Solve once over the current snapshot and publish the assignments.
        With ``commit`` the chosen units are marked ASSIGNED. (Direct one-shot
        entry point; :meth:`tick` is the cadence used by the nodes.)"""
        ts = self._clock if timestamp is None else timestamp
        result = self._solve(commit=commit, timestamp=ts)
        for a in result.assignments:
            self._bus.publish(Topics.GS_ASSIGNMENTS, a)
        if self._on_assignments is not None:
            self._on_assignments(result, ts)
        return result

    def tick(self, timestamp: float | None = None) -> AssignmentResult | None:
        """Advance one cadence step. Returns the result on a tick that *solved*,
        else None. Republishes the standing committed plan every tick."""
        ts = self._clock if timestamp is None else timestamp
        fired: AssignmentResult | None = None

        if self._mode == "continuous":
            fired = self._solve(commit=True, timestamp=ts)
            self._plan.extend(fired.assignments)
            if fired.assignments and self._on_assignments is not None:
                self._on_assignments(fired, ts)
        elif not self._fired:  # oneshot, awaiting go-signal
            ids = frozenset(t.track_id for t in self._current_threats())
            self._stable = self._stable + 1 if (ids and ids == self._last_ids) else 0
            self._last_ids = ids
            if ids and self._stable >= self._stable_ticks:
                fired = self._solve(commit=True, timestamp=ts)
                self._plan = list(fired.assignments)
                self._fired = True
                if self._on_assignments is not None:
                    self._on_assignments(fired, ts)

        if self._plan:
            self._republish(ts)
        return fired
