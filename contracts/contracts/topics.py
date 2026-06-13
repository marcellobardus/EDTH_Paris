"""
Canonical topic registry — the single source of truth for *every* channel in
the system, what message contract flows through it, and who is on each end.

Two ways to use this file:

1. ``Topics`` — bare string constants / helpers. Import these to publish or
   subscribe; never hardcode a topic string anywhere else.
       bus.publish(Topics.GS_ASSIGNMENTS, assignments)
       bus.subscribe(Topics.interceptor_claim("i2"), Claim, on_claim)

2. ``REGISTRY`` — a typed table of :class:`TopicSpec`. Each entry pins the
   exact contract type that flows, whether it is a single message or a batch,
   the publisher, the subscribers, the engagement phase, and a note. This is
   the reference humans read and the map code can drive (look up the message
   type for a topic, list what a role subscribes to, etc.).

Conventions: all positions are ``(x, y, z)`` in metres; all timestamps are
``float`` seconds since scenario start. Topics with ``{id}`` are per
interceptor — render them with ``spec.of("i1")`` or the ``Topics`` helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.messages import (
    Assignment,
    Claim,
    Commit,
    EngagementEvent,
    GroundTruth,
    InterceptorState,
    RadarDetection,
    ThreatAssessment,
    Track,
    WaypointCommand,
)


class Topics:
    """Bare topic names. Import from here — never hardcode strings."""

    # Simulation → Ground Station
    RADAR_DETECTIONS = "/radar/detections"          # RadarDetection[]

    # Ground Station internal / → agents
    GS_TRACKS = "/gs/tracks"                        # Track[]
    GS_THREATS = "/gs/threats"                      # ThreatAssessment[]
    GS_ASSIGNMENTS = "/gs/assignments"              # Assignment[]

    # Simulation → Visualization
    GROUND_TRUTH = "/simulation/ground_truth"       # GroundTruth
    ENGAGEMENT = "/simulation/engagement"           # EngagementEvent

    # Interceptor peer-to-peer (parametric — use helpers below)
    @staticmethod
    def interceptor_state(interceptor_id: str) -> str:
        return f"/interceptors/{interceptor_id}/state"       # InterceptorState

    @staticmethod
    def interceptor_claim(interceptor_id: str) -> str:
        return f"/interceptors/{interceptor_id}/claim"       # Claim

    @staticmethod
    def interceptor_commit(interceptor_id: str) -> str:
        return f"/interceptors/{interceptor_id}/commit"      # Commit

    # Interceptor → Simulation
    @staticmethod
    def waypoint_command(interceptor_id: str) -> str:
        return f"/interceptors/{interceptor_id}/waypoint"    # WaypointCommand


# ---------------------------------------------------------------------------
# Typed registry
# ---------------------------------------------------------------------------

# Roles (who is on each end of a topic).
SIM = "sim"          # Team 1 — Gazebo world, sensors, engagement
GS = "gs"            # Team 2 — track fusion, threat scoring, optimizer (pre-launch)
AGENT = "agent"      # Team 3 — interceptor: guidance + consensus (one process per drone)
VIZ = "viz"          # Team 4 — dashboard, overlays, metrics

# Phases (when a topic is live).
PRE_LAUNCH = "pre-launch"      # ground station active, before interceptors launch
AT_LAUNCH = "at-launch"        # issued once, at the go signal
IN_FLIGHT = "in-flight"        # after launch (Situation B for the consensus topics)
ALWAYS = "always"              # live for the whole scenario


@dataclass(frozen=True)
class TopicSpec:
    """One channel: the contract that flows through it and who is on each end."""

    pattern: str                       # concrete name, or template with "{id}"
    message: type[Any]                 # the contract dataclass that flows
    batch: bool                        # True => a list[message] is published per cycle
    publisher: str                     # role that publishes
    subscribers: tuple[str, ...]       # roles that subscribe
    phase: str                         # PRE_LAUNCH | AT_LAUNCH | IN_FLIGHT | ALWAYS
    note: str

    def of(self, interceptor_id: str) -> str:
        """Render a parametric topic for a specific interceptor."""
        return self.pattern.format(id=interceptor_id)

    @property
    def parametric(self) -> bool:
        return "{id}" in self.pattern


REGISTRY: tuple[TopicSpec, ...] = (
    # --- Sensing → Ground Station (pre-launch intelligence) ------------------
    TopicSpec(
        pattern="/radar/detections",
        message=RadarDetection,
        batch=True,
        publisher=SIM,
        subscribers=(GS,),
        phase=PRE_LAUNCH,
        note="Noisy radar hits (one batch per radar cycle); feeds Kalman fusion.",
    ),
    TopicSpec(
        pattern="/gs/tracks",
        message=Track,
        batch=True,
        publisher=GS,
        subscribers=(GS, AGENT, VIZ),
        phase=ALWAYS,
        note=(
            "Fused target picture (Kalman). Agents also read this in-flight for PN "
            "guidance; the `alive` flag flipping False is how agents learn a target "
            "died and re-task. (Open team decision: who keeps publishing tracks "
            "after launch, since GS officially ends at launch.)"
        ),
    ),
    TopicSpec(
        pattern="/gs/threats",
        message=ThreatAssessment,
        batch=True,
        publisher=GS,
        subscribers=(GS,),
        phase=PRE_LAUNCH,
        note="Scored/ranked tracks; input to the Hungarian assignment optimizer.",
    ),
    TopicSpec(
        pattern="/gs/assignments",
        message=Assignment,
        batch=True,
        publisher=GS,
        subscribers=(AGENT,),
        phase=AT_LAUNCH,
        note="Seed (interceptor -> track) assignment, issued once at the go signal.",
    ),
    # --- Simulation -> Visualization / world events --------------------------
    TopicSpec(
        pattern="/simulation/ground_truth",
        message=GroundTruth,
        batch=False,
        publisher=SIM,
        subscribers=(VIZ,),
        phase=ALWAYS,
        note="True poses of all objects (container message). Visualization only.",
    ),
    TopicSpec(
        pattern="/simulation/engagement",
        message=EngagementEvent,
        batch=False,
        publisher=SIM,
        subscribers=(VIZ,),
        phase=ALWAYS,
        note=(
            "Kill / miss event. Drives metrics + attrition. Agents normally learn "
            "of kills via Track.alive on /gs/tracks rather than subscribing here."
        ),
    ),
    # --- Interceptor peer-to-peer (CONSENSUS — Team 3 / your domain) ----------
    TopicSpec(
        pattern="/interceptors/{id}/state",
        message=InterceptorState,
        batch=False,
        publisher=AGENT,
        subscribers=(AGENT, VIZ),
        phase=IN_FLIGHT,
        note=(
            "CONSENSUS INPUT. 5 Hz current-state snapshot broadcast by each drone; "
            "peers fold the latest-per-peer snapshot into their coverage picture."
        ),
    ),
    TopicSpec(
        pattern="/interceptors/{id}/claim",
        message=Claim,
        batch=False,
        publisher=AGENT,
        subscribers=(AGENT,),
        phase=IN_FLIGHT,
        note="CONSENSUS. Re-tasking claim; carries `score` (highest score wins, id ties).",
    ),
    TopicSpec(
        pattern="/interceptors/{id}/commit",
        message=Commit,
        batch=False,
        publisher=AGENT,
        subscribers=(AGENT,),
        phase=IN_FLIGHT,
        note="CONSENSUS. Re-tasking commit after a claim wins; peers update coverage.",
    ),
    # --- Interceptor -> Simulation -------------------------------------------
    TopicSpec(
        pattern="/interceptors/{id}/waypoint",
        message=WaypointCommand,
        batch=False,
        publisher=AGENT,
        subscribers=(SIM,),
        phase=IN_FLIGHT,
        note="PN pursuit point (~10 Hz) the drone flies toward. Sent in both A and B.",
    ),
)


# Quick lookups -------------------------------------------------------------

def spec_for(pattern: str) -> TopicSpec:
    """Return the TopicSpec for a topic pattern (e.g. '/interceptors/{id}/claim')."""
    for spec in REGISTRY:
        if spec.pattern == pattern:
            return spec
    raise KeyError(pattern)


def subscribed_by(role: str) -> tuple[TopicSpec, ...]:
    """Topics a given role subscribes to (handy when wiring an agent's subs)."""
    return tuple(spec for spec in REGISTRY if role in spec.subscribers)


def published_by(role: str) -> tuple[TopicSpec, ...]:
    """Topics a given role publishes."""
    return tuple(spec for spec in REGISTRY if spec.publisher == role)
