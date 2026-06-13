"""
Threat assessor — the scoring layer between track fusion and assignment.

Scores a single fused ``Track`` against the defended asset, producing a
``ThreatAssessment`` with a positive, urgency-monotone ``threat_score`` and an
``eta_seconds`` time-to-impact. Pure and stateless: scoring is per-track and
independent (no cross-track coupling like tracking has), so the score formula is
the only thing that ever changes, and it is trivially unit-testable.

Threat model (v1): a drone is dangerous to the extent it is *closing* on the
asset. From the track's straight-line, constant-velocity extrapolation we take
the radial speed toward the asset; ``eta`` is distance / closing speed, and the
score is ``1/eta`` (imminent threats score highest). A non-closing track (one
that is receding or merely crossing) gets a sentinel eta and a near-zero — but
strictly positive — score, so the downstream assignment cost
``intercept_time / threat_score`` never divides by zero.
"""

from __future__ import annotations

import math

from contracts.messages import ThreatAssessment, Track

Vec3 = tuple[float, float, float]

ETA_SENTINEL = 1e9  # seconds; "not closing / never arrives" (finite, JSON-safe)
EPS_ETA = 1e-3  # floor so threat_score stays finite for an at-asset track


class ThreatAssessor:
    """Scores one track against a fixed defended asset. Stateless.

    ``min_closing_speed`` (m/s) is the threshold below which a track counts as
    not inbound (receding or crossing); its eta becomes ``ETA_SENTINEL`` and its
    threat_score collapses toward zero.
    """

    def __init__(self, target_position: Vec3, *, min_closing_speed: float = 1.0) -> None:
        self._target = target_position
        self._min_closing = min_closing_speed

    def assess(self, track: Track) -> ThreatAssessment:
        eta = self._eta_seconds(track.position, track.velocity)
        return ThreatAssessment(
            track_id=track.track_id,
            position=track.position,
            velocity=track.velocity,
            threat_score=1.0 / max(eta, EPS_ETA),
            eta_seconds=eta,
            timestamp=track.timestamp,
        )

    def _eta_seconds(self, position: Vec3, velocity: Vec3) -> float:
        tx, ty, tz = self._target
        dx, dy, dz = tx - position[0], ty - position[1], tz - position[2]
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance < 1e-6:
            return 0.0  # already at the asset — maximally urgent
        # Radial speed toward the asset (projection of velocity onto line-of-sight).
        closing = (velocity[0] * dx + velocity[1] * dy + velocity[2] * dz) / distance
        if closing <= self._min_closing:
            return ETA_SENTINEL  # receding or crossing — not a closing threat
        return distance / closing
