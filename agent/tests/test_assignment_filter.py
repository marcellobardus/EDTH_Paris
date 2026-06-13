"""A1 tests: an interceptor keeps only its own assignment."""

from agent.local_state import InterceptorLocalState, select_assignment
from contracts.messages import Assignment


def _asg(iid: str, track: str, wp=(0.0, 0.0, 0.0), t=0.0) -> Assignment:
    return Assignment(interceptor_id=iid, track_id=track, initial_waypoint=wp, timestamp=t)


def test_select_assignment_picks_own() -> None:
    batch = [_asg("i1", "t1"), _asg("i2", "t2"), _asg("i3", "t3")]
    mine = select_assignment(batch, "i2")
    assert mine is not None and mine.track_id == "t2"


def test_select_assignment_ignores_others() -> None:
    batch = [_asg("i1", "t1"), _asg("i3", "t3")]
    assert select_assignment(batch, "i2") is None


def test_select_assignment_last_duplicate_wins() -> None:
    batch = [_asg("i1", "t1", t=0.0), _asg("i1", "t9", t=1.0)]
    mine = select_assignment(batch, "i1")
    assert mine is not None and mine.track_id == "t9"


def test_apply_assignment_updates_state() -> None:
    st = InterceptorLocalState("i1", launch_position=(480.0, 480.0, 0.0))
    assert st.assigned_track_id is None

    changed = st.apply_assignments([_asg("i2", "t2"), _asg("i1", "t1", wp=(10.0, 20.0, 0.0))])
    assert changed is True
    assert st.assigned_track_id == "t1"
    assert st.initial_waypoint == (10.0, 20.0, 0.0)

    # Re-applying the same assignment is a no-op.
    assert st.apply_assignments([_asg("i1", "t1", wp=(10.0, 20.0, 0.0))]) is False


def test_apply_assignment_for_other_is_noop() -> None:
    st = InterceptorLocalState("i1", launch_position=(0.0, 0.0, 0.0))
    assert st.apply_assignments([_asg("i2", "t2")]) is False
    assert st.assigned_track_id is None


def test_state_msg_reflects_assignment() -> None:
    st = InterceptorLocalState("i1", launch_position=(480.0, 480.0, 0.0))
    st.apply_assignments([_asg("i1", "t1")])
    msg = st.to_state_msg(timestamp=3.5)
    assert msg.interceptor_id == "i1"
    assert msg.assigned_track_id == "t1"
    assert msg.position == (480.0, 480.0, 0.0)
    assert msg.alive is True
    assert msg.timestamp == 3.5
