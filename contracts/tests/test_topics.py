"""Guard the topic registry: every Topics string must have a typed REGISTRY entry."""

from __future__ import annotations

from contracts.topics import REGISTRY, Topics, spec_for, subscribed_by


def test_static_topics_are_registered() -> None:
    for name in (
        Topics.RADAR_DETECTIONS,
        Topics.GS_TRACKS,
        Topics.GS_THREATS,
        Topics.GS_ASSIGNMENTS,
        Topics.GROUND_TRUTH,
        Topics.ENGAGEMENT,
    ):
        assert spec_for(name).pattern == name


def test_parametric_topics_match_helpers() -> None:
    # The Topics helper and the registry pattern must render the same string.
    assert spec_for("/interceptors/{id}/state").of("i1") == Topics.interceptor_state("i1")
    assert spec_for("/interceptors/{id}/claim").of("i1") == Topics.interceptor_claim("i1")
    assert spec_for("/interceptors/{id}/commit").of("i1") == Topics.interceptor_commit("i1")
    assert spec_for("/interceptors/{id}/waypoint").of("i1") == Topics.waypoint_command("i1")


def test_every_registry_entry_has_a_message_type() -> None:
    for spec in REGISTRY:
        assert isinstance(spec.message, type)
        assert spec.publisher and spec.subscribers


def test_agent_subscribes_to_consensus_and_tracks() -> None:
    patterns = {s.pattern for s in subscribed_by("agent")}
    assert "/interceptors/{id}/state" in patterns   # peer snapshots
    assert "/interceptors/{id}/claim" in patterns   # peer claims
    assert "/gs/tracks" in patterns                 # target picture for guidance
    assert "/gs/assignments" in patterns            # the seed
