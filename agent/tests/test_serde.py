"""Wire-codec tests: contract dataclass survives a JSON round-trip intact."""

from agent.serde import decode, encode
from contracts.messages import Assignment, InterceptorState


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
