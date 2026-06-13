"""
Ground-station launch decider.

Subscribes to the radar's ``RadarDetection`` stream and decides, per *new*
threat, whether to launch an interceptor. A new threat triggers a launch while
interceptors remain in the pool; once the pool is exhausted the decision is to
hold — "whether to launch or not."

Novelty here is naive nearest-neighbour gating (a detection far from every known
threat is "new"). It is a deliberate placeholder for proper Kalman track
fusion + threat scoring (Team 2's `track_fusion` / `threat_assessor`); the point
of this module is the *decision*, not the tracking.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from contracts.bus import Bus
from contracts.messages import RadarDetection
from contracts.topics import Topics

Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class LaunchDecision:
    """The decision taken when a new threat is first seen."""

    threat_id: str
    position: Vec3
    interceptor_id: str | None     # assigned interceptor, or None if held
    launched: bool
    reason: str
    timestamp: float


def _dist2(a: Vec3, b: Vec3) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


class LaunchDecider:
    """Turns a radar detection stream into per-threat launch decisions."""

    def __init__(
        self,
        bus: Bus,
        *,
        new_threat_radius_m: float = 100.0,
        interceptor_pool: int = 3,
        on_decision: Callable[[LaunchDecision], None] | None = None,
    ) -> None:
        self._radius2 = new_threat_radius_m**2
        self._pool = interceptor_pool
        self._on_decision = on_decision
        self._threats: dict[str, Vec3] = {}   # threat_id -> last known position
        self._next_threat = 1
        self._launched = 0
        self.decisions: list[LaunchDecision] = []
        bus.subscribe(Topics.RADAR_DETECTIONS, RadarDetection, self._on_detection)

    @property
    def threats_seen(self) -> int:
        return len(self._threats)

    @property
    def interceptors_committed(self) -> int:
        return self._launched

    def _match(self, position: Vec3) -> str | None:
        """Return the id of a known threat near `position`, else None (new)."""
        for threat_id, known in self._threats.items():
            if _dist2(position, known) <= self._radius2:
                return threat_id
        return None

    def _on_detection(self, detection: RadarDetection) -> None:
        matched = self._match(detection.position)
        if matched is not None:
            self._threats[matched] = detection.position   # track update, not new
            return

        threat_id = f"T{self._next_threat}"
        self._next_threat += 1
        self._threats[threat_id] = detection.position
        self._record(self._decide(threat_id, detection))

    def _decide(self, threat_id: str, detection: RadarDetection) -> LaunchDecision:
        if self._launched < self._pool:
            self._launched += 1
            return LaunchDecision(
                threat_id=threat_id,
                position=detection.position,
                interceptor_id=f"i{self._launched}",
                launched=True,
                reason="new threat — interceptor available, launch",
                timestamp=detection.timestamp,
            )
        return LaunchDecision(
            threat_id=threat_id,
            position=detection.position,
            interceptor_id=None,
            launched=False,
            reason="new threat — interceptor pool exhausted, hold",
            timestamp=detection.timestamp,
        )

    def _record(self, decision: LaunchDecision) -> None:
        self.decisions.append(decision)
        if self._on_decision is not None:
            self._on_decision(decision)
