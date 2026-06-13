"""Unit tests for the threat assessor (Phase 1)."""

from __future__ import annotations

import pytest
from contracts.messages import Track
from gs.threat_assessor import ETA_SENTINEL, ThreatAssessor

ORIGIN = (0.0, 0.0, 0.0)


def _track(position, velocity, *, tid="t1", t=1.0) -> Track:
    return Track(
        track_id=tid,
        position=position,
        velocity=velocity,
        covariance=[[0.0] * 6 for _ in range(6)],
        alive=True,
        timestamp=t,
    )


def test_head_on_inbound_eta_and_score() -> None:
    # 1000 m out on the +x axis, closing straight at the origin at 50 m/s.
    assessor = ThreatAssessor(ORIGIN)
    ta = assessor.assess(_track((1000.0, 0.0, 0.0), (-50.0, 0.0, 0.0)))
    assert ta.eta_seconds == pytest.approx(20.0)
    assert ta.threat_score == pytest.approx(0.05)


def test_closer_and_faster_score_higher() -> None:
    assessor = ThreatAssessor(ORIGIN)
    base = assessor.assess(_track((1000.0, 0.0, 0.0), (-50.0, 0.0, 0.0))).threat_score
    closer = assessor.assess(_track((500.0, 0.0, 0.0), (-50.0, 0.0, 0.0))).threat_score
    faster = assessor.assess(_track((1000.0, 0.0, 0.0), (-100.0, 0.0, 0.0))).threat_score
    assert closer > base  # half the distance -> double the urgency
    assert faster > base  # twice the speed -> double the urgency


def test_receding_track_is_not_a_threat() -> None:
    assessor = ThreatAssessor(ORIGIN)
    ta = assessor.assess(_track((1000.0, 0.0, 0.0), (50.0, 0.0, 0.0)))  # moving away
    assert ta.eta_seconds == ETA_SENTINEL
    assert ta.threat_score > 0.0  # strictly positive — keeps assignment cost finite
    assert ta.threat_score < 1e-6


def test_crossing_track_is_not_closing() -> None:
    # Velocity perpendicular to the line-of-sight: not approaching the asset.
    assessor = ThreatAssessor(ORIGIN)
    ta = assessor.assess(_track((1000.0, 0.0, 0.0), (0.0, 60.0, 0.0)))
    assert ta.eta_seconds == ETA_SENTINEL


def test_at_asset_is_max_urgency() -> None:
    assessor = ThreatAssessor(ORIGIN)
    ta = assessor.assess(_track((0.0, 0.0, 0.0), (-50.0, 0.0, 0.0)))
    assert ta.eta_seconds == 0.0
    assert ta.threat_score == pytest.approx(1000.0)  # 1 / EPS_ETA


def test_passthrough_fields_preserved() -> None:
    assessor = ThreatAssessor(ORIGIN)
    track = _track((1500.0, -200.0, 120.0), (-40.0, 5.0, 0.0), tid="shahed-7", t=12.5)
    ta = assessor.assess(track)
    assert ta.track_id == "shahed-7"
    assert ta.position == (1500.0, -200.0, 120.0)
    assert ta.velocity == (-40.0, 5.0, 0.0)
    assert ta.timestamp == 12.5


def test_assignment_cost_is_always_finite() -> None:
    # C = intercept_time / threat_score must never blow up, for any track.
    assessor = ThreatAssessor(ORIGIN)
    for vel in [(-50.0, 0.0, 0.0), (50.0, 0.0, 0.0), (0.0, 50.0, 0.0), (0.0, 0.0, 0.0)]:
        ta = assessor.assess(_track((1000.0, 0.0, 0.0), vel))
        cost = 30.0 / ta.threat_score
        assert cost < 1e12 and cost == cost  # finite, not NaN


def test_off_origin_asset() -> None:
    # Asset at (500, 500, 0); a track inbound along the diagonal at 10 m/s.
    assessor = ThreatAssessor((500.0, 500.0, 0.0))
    ta = assessor.assess(_track((1500.0, 1500.0, 0.0), (-10.0, -10.0, 0.0)))
    # distance = sqrt(2)*1000 ≈ 1414; closing speed = sqrt(2)*10 ≈ 14.14; eta ≈ 100 s.
    assert ta.eta_seconds == pytest.approx(100.0, rel=1e-3)
