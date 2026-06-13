"""
Shared message contracts for all teams.
All positions are (x, y, z) in metres. All timestamps are float seconds since scenario start.
DO NOT define message formats outside this file.
"""

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Team 1 → Team 2  (Simulation → Ground Station)
# ---------------------------------------------------------------------------


@dataclass
class RadarDetection:
    """Raw hit from a single radar. Position is noisy."""

    radar_id: str
    position: tuple[float, float, float]  # noisy (x, y, z) in metres
    timestamp: float


@dataclass
class GroundTruthObject:
    object_id: str
    kind: str  # "interceptor" | "shahed"
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
    success: bool  # True = kill, False = miss / expired
    position: tuple[float, float, float]  # location of engagement
    timestamp: float


# ---------------------------------------------------------------------------
# Team 2 internal + Team 2 → Team 3  (Ground Station)
# ---------------------------------------------------------------------------


@dataclass
class Track:
    """Fused, filtered estimate of a single Shahed."""

    track_id: str
    position: tuple[float, float, float]  # Kalman estimate (x, y, z)
    velocity: tuple[float, float, float]  # Kalman estimate
    covariance: list[list[float]]  # 6×6 state covariance
    alive: bool  # False once engagement confirms kill
    timestamp: float


@dataclass
class ThreatAssessment:
    """Scored track ready for the assignment optimizer."""

    track_id: str
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    threat_score: float  # higher = more dangerous
    eta_seconds: float  # estimated time to target
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
    Broadcast by each interceptor at 5 Hz — the SINGLE peer-to-peer message of
    the CBAA re-tasking protocol (the old Claim/Commit pair is gone). Peers use
    it both to maintain their local awareness picture AND to arbitrate ownership.

    Decentralised re-tasking is consensus-by-broadcast: every interceptor
    publishes which track it `owns`, the self-computed `owns_priority` key that
    justifies that ownership, and whether it has `locked` on for terminal
    guidance. Peers never recompute a peer's key — they compare the transmitted
    `owns_priority` directly, so all agents arbitrate on identical numbers.
    """

    interceptor_id: str
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    assigned_track_id: str | None  # the owned track (spec: `owns`); None if free
    alive: bool
    timestamp: float
    # --- CBAA fields (default so pre-CBAA constructors keep working) ----------
    # Lexicographic priority key for `assigned_track_id`, computed by THIS owner
    # and never recomputed by peers: (affinity_bucket, danger, id_rank), larger
    # wins. None iff assigned_track_id is None. Travels as a JSON list on the
    # wire; awareness normalises it back to a tuple on receive.
    owns_priority: tuple[float, float, float] | None = None
    locked: bool = False  # monotone: terminal lock, never released once True
    seq: int = 0  # per-sender monotone counter (anti-replay / staleness guard)


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
    position: tuple[float, float, float]  # PN pursuit point
    timestamp: float
