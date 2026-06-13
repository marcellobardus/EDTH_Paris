"""Shared contracts: message types, the topic registry, and the config schema."""

from contracts.config import ScenarioConfig
from contracts.messages import (
    Assignment,
    Claim,
    Commit,
    EngagementEvent,
    GroundTruth,
    GroundTruthObject,
    InterceptorState,
    RadarDetection,
    ThreatAssessment,
    Track,
    WaypointCommand,
)
from contracts.topics import (
    REGISTRY,
    Topics,
    TopicSpec,
    published_by,
    spec_for,
    subscribed_by,
)

__all__ = [
    # config
    "ScenarioConfig",
    # messages
    "Assignment",
    "Claim",
    "Commit",
    "EngagementEvent",
    "GroundTruth",
    "GroundTruthObject",
    "InterceptorState",
    "RadarDetection",
    "ThreatAssessment",
    "Track",
    "WaypointCommand",
    # topics
    "REGISTRY",
    "TopicSpec",
    "Topics",
    "published_by",
    "spec_for",
    "subscribed_by",
]
