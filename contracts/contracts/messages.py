"""
Shared message contracts for all teams.
All positions are (x, y, z) in metres. All timestamps are float seconds since scenario start.
DO NOT define message formats outside this file.
"""

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Team 1 → Team 2  (Simulation → Ground Station)
# ---------------------------------------------------------------------------

@dataclass
class RadarDetection:
    """Raw hit from a single radar. Position is noisy."""
    radar_id: str
    position: tuple[float, float, float]   # noisy (x, y, z) in metres
    timestamp: float


@dataclass
class GroundTruthObject:
    object_id: str
    kind: str                              # "interceptor" | "shahed"
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    alive: bool


@dataclass
class GroundTruth:
    """Broadcast by simulation every physics step. For visualisation only."""
    objects: list[GroundTruthObject]
    timestamp: float


@dataclass
class EngagementEvent:
    """Fired when an interceptor kills a Shahed (or misses on timeout)."""
    interceptor_id: str
    track_id: str
    success: bool                          # True = kill, False = miss / expired
    position: tuple[float, float, float]  # location of engagement
    timestamp: float


# ---------------------------------------------------------------------------
# Team 2 internal + Team 2 → Team 3  (Ground Station)
# ---------------------------------------------------------------------------

@dataclass
class Track:
    """Fused, filtered estimate of a single Shahed."""
    track_id: str
    position: tuple[float, float, float]   # Kalman estimate (x, y, z)
    velocity: tuple[float, float, float]   # Kalman estimate
    covariance: list[list[float]]          # 6×6 state covariance
    alive: bool                            # False once engagement confirms kill
    timestamp: float


@dataclass
class ThreatAssessment:
    """Scored track ready for the assignment optimizer."""
    track_id: str
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    threat_score: float                    # higher = more dangerous
    eta_seconds: float                     # estimated time to target
    timestamp: float


@dataclass
class Assignment:
    """
    Initial (interceptor → track) pair issued by the Ground Station at launch.
    One Assignment per interceptor. interceptor_id matches the id the agent
    will use for all its topic publications.
    """
    interceptor_id: str
    track_id: str
    initial_waypoint: tuple[float, float, float]  # first PN pursuit point
    timestamp: float


# ---------------------------------------------------------------------------
# Team 3 peer-to-peer  (Interceptor Agent ↔ Interceptor Agent)
# ---------------------------------------------------------------------------

@dataclass
class InterceptorState:
    """
    Broadcast by each interceptor at 5 Hz.
    Peers use this to maintain their local awareness picture.
    """
    interceptor_id: str
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    assigned_track_id: Optional[str]       # None if free
    alive: bool
    timestamp: float


@dataclass
class Claim:
    """
    Broadcast during claim-and-confirm re-tasking.
    An interceptor claims it intends to pursue target_track_id.
    Higher interceptor_id wins conflicts on the same target.
    """
    interceptor_id: str
    target_track_id: str
    timestamp: float


@dataclass
class Commit:
    """
    Broadcast after a claim is confirmed (no higher-ID competing claim received).
    All peers must update their local picture when they receive this.
    """
    interceptor_id: str
    target_track_id: str
    timestamp: float


# ---------------------------------------------------------------------------
# Team 3 → Team 1  (Interceptor Agent → Simulation)
# ---------------------------------------------------------------------------

@dataclass
class WaypointCommand:
    """
    Sent by each interceptor agent at ~10 Hz to the simulation.
    The simulation moves the interceptor body toward this point.
    """
    interceptor_id: str
    position: tuple[float, float, float]   # PN pursuit point
    timestamp: float
