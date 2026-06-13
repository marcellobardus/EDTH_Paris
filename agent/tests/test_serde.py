"""Wire-codec tests: contract dataclass survives a JSON round-trip intact."""

from agent.serde import decode, decode_list, encode, encode_list
from contracts.messages import Assignment, InterceptorState, Track


def test_assignment_roundtrip() -> None:
    a = Assignment(
        interceptor_id="i1",
        track_id="t7",
        initial_waypoint=(12.5, -3.0, 0.0),
        timestamp=4.2,
    )
    back = decode(encode(a), Assignment)
    assert back == a
    # JSON has no tuple type; the codec must restore it, not leave a list.
    assert isinstance(back.initial_waypoint, tuple)


def test_interceptor_state_roundtrip_preserves_none() -> None:
    s = InterceptorState(
        interceptor_id="i2",
        position=(1.0, 2.0, 3.0),
        velocity=(0.0, 0.0, 0.0),
        assigned_track_id=None,
        alive=True,
        timestamp=0.0,
    )
    back = decode(encode(s), InterceptorState)
    assert back == s
    assert back.assigned_track_id is None
    assert isinstance(back.position, tuple)


def test_encode_rejects_non_dataclass() -> None:
    import pytest

    with pytest.raises(TypeError):
        encode({"not": "a dataclass"})


def test_assignment_list_roundtrip() -> None:
    # GS topics carry arrays (Assignment[], Track[]) in one std_msgs/String.
    batch = [
        Assignment("i1", "t1", (0.0, 0.0, 0.0), 0.0),
        Assignment("i2", "t2", (1.0, 2.0, 3.0), 0.0),
    ]
    back = decode_list(encode_list(batch), Assignment)
    assert back == batch


def test_track_list_roundtrip_keeps_list_and_tuple_fields() -> None:
    # Track mixes tuple fields (position/velocity) and a list field (covariance):
    # the codec must restore tuples but leave the list-typed covariance a list.
    cov = [[0.0] * 6 for _ in range(6)]
    tracks = [Track("t1", (1.0, 2.0, 3.0), (4.0, 5.0, 6.0), cov, True, 0.0)]
    back = decode_list(encode_list(tracks), Track)
    assert back == tracks
    assert isinstance(back[0].position, tuple)
    assert isinstance(back[0].covariance, list)
