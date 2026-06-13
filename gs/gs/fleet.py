"""
Interceptor fleet-state manager.

The ground station's single source of truth for its **own** interceptors — how
many, where, and what state each is in — so the assignment optimizer can ask
"which units are available, and where?" and the viewer / metrics can read one
coherent fleet picture.

Initialised from config (the pre-launch pad layout) and kept current from two
live streams: each interceptor's own :class:`InterceptorState` broadcast and
:class:`EngagementEvent` outcomes. The object holds **no bus** — a node wires the
bus to its handlers — so it is deterministic and unit-testable.

Lifecycle (monotonic toward the terminal states ``EXPENDED`` / ``DOWN``):

    READY ──assign──► ASSIGNED ──state──► IN_FLIGHT ──engagement──► EXPENDED
      └──────────────────── alive == False ───────────────────────► DOWN

Positions are resolved **GS-side** (no ``contracts`` change): ``count`` units are
placed on a defensive ring around the configured ``launch_position``, or taken
verbatim from an explicit list. See ``FLEET_STATE_PLAN.md``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum

from contracts.config import ScenarioConfig
from contracts.messages import EngagementEvent, InterceptorState

Vec3 = tuple[float, float, float]
log = logging.getLogger("gs.fleet")


class Status(Enum):
    READY = "ready"  # at its site, assignable by the optimizer
    ASSIGNED = "assigned"  # committed to a track pre-launch, not yet launched
    IN_FLIGHT = "in_flight"  # launched and pursuing (its state broadcast seen)
    EXPENDED = "expended"  # engagement resolved — kill or miss
    DOWN = "down"  # lost / offline (alive == False)


_TERMINAL = (Status.EXPENDED, Status.DOWN)


@dataclass(frozen=True)
class Interceptor:
    """An assignable interceptor as the optimizer sees it.

    Kinetic power is uniform across the fleet, so the only differentiators are
    kinematic: where it is, how fast it flies, how far it reaches.
    """

    interceptor_id: str
    position: Vec3
    speed_mps: float
    range_m: float


@dataclass
class FleetUnit:
    """One interceptor's full state in the GS picture."""

    interceptor_id: str
    position: Vec3  # its site pre-launch; live position once in flight
    velocity: Vec3
    speed_mps: float
    range_m: float
    status: Status
    assigned_track_id: str | None
    alive: bool
    last_update: float  # scenario seconds of the last state touch

    def as_interceptor(self) -> Interceptor:
        return Interceptor(self.interceptor_id, self.position, self.speed_mps, self.range_m)


def interceptor_id(index: int) -> str:
    """The canonical interceptor id for a 0-based fleet index (``i1``, ``i2`` …).

    Single source of truth for the format — the agent (Team 3) keys on the same
    ids, so the fleet must never grow a second copy of this rule.
    """
    return f"i{index + 1}"


def ring_positions(center: Vec3, count: int, radius: float) -> list[Vec3]:
    """``count`` distinct sites evenly spaced on a circle of ``radius`` around
    ``center`` (altitude preserved). A single unit sits at the centre."""
    if count <= 1:
        return [center]
    cx, cy, cz = center
    return [
        (
            cx + radius * math.cos(2.0 * math.pi * i / count),
            cy + radius * math.sin(2.0 * math.pi * i / count),
            cz,
        )
        for i in range(count)
    ]


class InterceptorFleet:
    """The GS's model of its interceptor fleet. Bus-free; a node feeds its
    handlers and reads :meth:`available` / :meth:`snapshot` / :meth:`counts`."""

    def __init__(self, units: list[FleetUnit]) -> None:
        self._units: dict[str, FleetUnit] = {u.interceptor_id: u for u in units}

    @classmethod
    def from_config(
        cls,
        cfg: ScenarioConfig,
        *,
        center: Vec3 | None = None,
        ring_radius: float = 300.0,
        speed: float | None = None,
        range_m: float | None = None,
        positions: list[Vec3] | None = None,
    ) -> InterceptorFleet:
        """Build ``count`` READY units from config.

        Positions come from an explicit ``positions`` list (verbatim) or a
        defensive ring of ``ring_radius`` around ``center`` (default: the config
        ``launch_position`` — pass the defended asset to centre the ring on it).
        ``speed``/``range_m`` override the config kinematics when given.
        """
        ic = cfg.interceptors
        ctr = ic.launch_position if center is None else center
        spd = ic.speed_mps if speed is None else speed
        rg = ic.range_m if range_m is None else range_m
        if positions is None:
            positions = ring_positions(ctr, ic.count, ring_radius)
        elif len(positions) != ic.count:
            raise ValueError(f"positions has {len(positions)} entries, expected count={ic.count}")
        units = [
            FleetUnit(
                interceptor_id=interceptor_id(i),
                position=positions[i],
                velocity=(0.0, 0.0, 0.0),
                speed_mps=spd,
                range_m=rg,
                status=Status.READY,
                assigned_track_id=None,
                alive=True,
                last_update=0.0,
            )
            for i in range(ic.count)
        ]
        return cls(units)

    # -- queries -------------------------------------------------------------

    def available(self) -> list[Interceptor]:
        """READY units only — exactly the pool the optimizer assigns over."""
        return [u.as_interceptor() for u in self._units.values() if u.status is Status.READY]

    def snapshot(self) -> list[FleetUnit]:
        """The full fleet, for the viewer / metrics."""
        return list(self._units.values())

    def counts(self) -> dict[Status, int]:
        c = dict.fromkeys(Status, 0)
        for u in self._units.values():
            c[u.status] += 1
        return c

    def get(self, interceptor_id: str) -> FleetUnit | None:
        return self._units.get(interceptor_id)

    # -- transitions ---------------------------------------------------------

    def mark_assigned(self, interceptor_id: str, track_id: str) -> None:
        """Commit a READY unit to a track (called by the optimizer's publisher)."""
        u = self._units.get(interceptor_id)
        if u is None:
            log.warning("mark_assigned: unknown interceptor %s", interceptor_id)
            return
        if u.status is not Status.READY:
            log.warning(
                "mark_assigned: %s is %s, not READY — ignoring", interceptor_id, u.status.value
            )
            return
        u.status = Status.ASSIGNED
        u.assigned_track_id = track_id

    def on_interceptor_state(self, state: InterceptorState) -> None:
        """Live position/velocity/assignment from a /interceptors/{id}/state
        broadcast. First broadcast after launch promotes to IN_FLIGHT; a dead
        interceptor goes DOWN. Terminal units are sticky (no resurrection)."""
        u = self._units.get(state.interceptor_id)
        if u is None:
            log.debug("ignoring state for unknown interceptor %s", state.interceptor_id)
            return
        if u.status in _TERMINAL:
            return  # monotonic lifecycle — never revive an expended/down unit
        u.position = state.position
        u.velocity = state.velocity
        u.assigned_track_id = state.assigned_track_id
        u.alive = state.alive
        u.last_update = state.timestamp
        if not state.alive:
            u.status = Status.DOWN
        elif u.status in (Status.READY, Status.ASSIGNED):
            # First broadcast => launched. READY (not just ASSIGNED) is allowed on
            # purpose: in the distributed system an interceptor's state broadcast
            # can overtake the GS's own mark_assigned, so a unit may appear in
            # flight before we recorded the commit. Treat the live broadcast as
            # ground truth rather than dropping it.
            u.status = Status.IN_FLIGHT

    def on_engagement(self, event: EngagementEvent) -> None:
        """Engagement outcome (kill or miss) — the interceptor is spent."""
        u = self._units.get(event.interceptor_id)
        if u is None:
            log.debug("ignoring engagement for unknown interceptor %s", event.interceptor_id)
            return
        if u.status in _TERMINAL:
            return  # first terminal signal wins
        u.status = Status.EXPENDED
        u.last_update = event.timestamp
