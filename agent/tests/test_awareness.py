"""A4 tests (FR-7.3 / FR-8.1): local picture + architecture §4 conflict predicate."""

from agent.awareness import AwarenessPicture
from contracts.messages import Commit, EngagementEvent, InterceptorState, Track


def _state(iid: str, track: str | None, *, alive: bool = True, t: float = 0.0) -> InterceptorState:
    return InterceptorState(iid, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), track, alive, t)


def _track(tid: str, *, alive: bool = True) -> Track:
    cov = [[0.0] * 6 for _ in range(6)]
    return Track(tid, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), cov, alive, 0.0)


def _picture(self_id: str = "i1", timeout: float = 1.5) -> AwarenessPicture:
    return AwarenessPicture(self_id, timeout)


def test_bijective_assignment_has_no_conflict() -> None:
    p = _picture()
    p.on_tracks([_track("t1"), _track("t2"), _track("t3")])
    p.update_self("t1", True, 0.0)
    p.on_peer_state(_state("i2", "t2"))
    p.on_peer_state(_state("i3", "t3"))
    assert p.coverage() == {"t1": ["i1"], "t2": ["i2"], "t3": ["i3"]}
    assert p.uncovered_active_tracks() == set()
    assert p.has_coverage_conflict() is False


def test_double_cover_plus_uncovered_is_a_conflict() -> None:
    # i2 and i3 both on t2 (waste); t3 active but uncovered -> conjunctive conflict.
    p = _picture()
    p.on_tracks([_track("t1"), _track("t2"), _track("t3")])
    p.update_self("t1", True, 0.0)
    p.on_peer_state(_state("i2", "t2"))
    p.on_peer_state(_state("i3", "t2"))
    assert p.uncovered_active_tracks() == {"t3"}
    assert p.has_coverage_conflict() is True


def test_uncovered_without_spare_is_not_a_conflict() -> None:
    # More active tracks than interceptors, but everyone is on a distinct live
    # track -> no spare to reassign, so the conjunctive predicate stays quiet.
    p = _picture()
    p.on_tracks([_track("t1"), _track("t2"), _track("t3")])
    p.update_self("t1", True, 0.0)
    p.on_peer_state(_state("i2", "t2"))
    assert p.uncovered_active_tracks() == {"t3"}
    assert p.has_coverage_conflict() is False


def test_assigned_to_dead_track_is_wasted() -> None:
    # i2 killed t2 (now dead) but still holds the assignment; t3 uncovered.
    p = _picture()
    p.on_tracks([_track("t1"), _track("t2", alive=False), _track("t3")])
    p.update_self("t1", True, 0.0)
    p.on_peer_state(_state("i2", "t2"))
    assert p.is_dead("t2") is True
    assert p.uncovered_active_tracks() == {"t3"}
    assert p.has_coverage_conflict() is True


def test_engagement_event_marks_track_dead() -> None:
    p = _picture()
    p.on_tracks([_track("t1"), _track("t3")])
    p.update_self("t1", True, 0.0)
    p.on_peer_state(_state("i2", "t1"))  # i1+i2 both on t1 (waste)
    p.on_engagement(EngagementEvent("i9", "t9", True, (0.0, 0.0, 0.0), 0.0))
    assert p.is_dead("t9") is True


def test_dead_interceptor_frees_its_track() -> None:
    # i2 lost (alive=False) -> t2 uncovered; i1 doubles t1's... no: i1 alone on t1.
    p = _picture()
    p.on_tracks([_track("t1"), _track("t2")])
    p.update_self("t1", True, 0.0)
    p.on_peer_state(_state("i2", "t2", alive=False))
    assert p.coverage() == {"t1": ["i1"]}
    assert p.uncovered_active_tracks() == {"t2"}


def test_stale_peer_still_covers_its_track() -> None:
    # Conservative Q2: silence does not free coverage.
    p = _picture(timeout=1.5)
    p.on_tracks([_track("t1"), _track("t2")])
    p.update_self("t1", True, 10.0)
    p.on_peer_state(_state("i2", "t2", t=0.0))  # last heard at t=0
    now = 5.0  # i2 is stale (5s > 1.5s)
    assert p.is_stale("i2", now) is True
    assert "t2" in p.coverage()  # still counts
    assert p.uncovered_active_tracks() == set()
    assert p.has_coverage_conflict() is False


def test_peer_commit_updates_picture_immediately() -> None:
    # FR-8.5: act on a Commit before the peer's next state arrives.
    p = _picture()
    p.on_tracks([_track("t1"), _track("t2"), _track("t3")])
    p.update_self("t1", True, 0.0)
    p.on_peer_state(_state("i2", "t2"))
    p.on_peer_state(_state("i3", "t2"))  # i2,i3 both on t2; t3 uncovered
    assert p.has_coverage_conflict() is True
    p.on_commit(Commit("i3", "t3", 0.5))  # i3 moves to t3
    assert p.coverage()["t3"] == ["i3"]
    assert p.has_coverage_conflict() is False
