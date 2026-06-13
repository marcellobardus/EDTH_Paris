"""Integration: radar -> bus -> TrackPublisher -> GS_TRACKS (Phase 5)."""

from __future__ import annotations

from datetime import datetime, timedelta

from agent.bus import MockBroker
from contracts.messages import Track
from contracts.topics import Topics
from gs.track_publisher import TrackPublisher
from sim.radar_stonesoup import StoneSoupRadar, TargetInit

T0 = datetime(2026, 6, 13, 12, 0, 0)

_THREE = [
    TargetInit("north", (0.0, 0.0, 3000.0, -50.0, 100.0, 0.0)),
    TargetInit("east", (3000.0, -50.0, 0.0, 0.0, 100.0, 0.0)),
    TargetInit("south", (0.0, 0.0, -3000.0, 50.0, 100.0, 0.0)),
]


def _wire() -> tuple[MockBroker, StoneSoupRadar, TrackPublisher, list[Track]]:
    broker = MockBroker()
    published: list[Track] = []
    broker.endpoint("sink").subscribe(Topics.GS_TRACKS, Track, published.append)
    publisher = TrackPublisher(broker.endpoint("gs"), start_time=T0)
    radar = StoneSoupRadar(
        broker.endpoint("radar1"), "radar1", _THREE, start_time=T0, seed=1, prob_detect=1.0
    )
    return broker, radar, publisher, published


def test_publishes_fused_tracks_on_gs_tracks() -> None:
    _, radar, publisher, published = _wire()

    last_tick: list[Track] = []
    for k in range(1, 16):
        radar.scan(T0 + timedelta(seconds=k))  # publishes detections into the buffer
        last_tick = publisher.tick()

    assert published, "expected Track messages on GS_TRACKS"
    assert all(isinstance(t, Track) for t in published)
    # After warm-up each tick emits exactly the three confirmed tracks.
    assert len(last_tick) == 3
    assert len({t.track_id for t in last_tick}) == 3


def test_published_track_carries_filtered_state() -> None:
    _, radar, publisher, _ = _wire()
    tracks: list[Track] = []
    for k in range(1, 16):
        radar.scan(T0 + timedelta(seconds=k))
        tracks = publisher.tick()

    t = tracks[0]
    assert t.alive is True
    assert len(t.position) == 3 and len(t.velocity) == 3
    assert len(t.covariance) == 6 and all(len(row) == 6 for row in t.covariance)
    # The north/east/south Shaheds all close at 50 m/s, so every track should
    # carry a non-trivial velocity estimate.
    assert any(abs(v) > 10.0 for v in t.velocity)


def test_buffer_refills_after_tick_swaps_it() -> None:
    # The method-bound handler must target the *current* buffer, so a scan that
    # arrives after a tick is still captured (a list.append bound at subscribe
    # time would orphan into the drained buffer and silently drop it).
    _, radar, publisher, _ = _wire()
    radar.scan(T0 + timedelta(seconds=1))
    assert len(publisher._buffer) == 3
    publisher.tick()
    assert publisher._buffer == []  # drained
    radar.scan(T0 + timedelta(seconds=2))
    assert len(publisher._buffer) == 3  # refilled, not orphaned


def test_empty_tick_is_a_noop() -> None:
    # A tick with no buffered detections must not advance the tracker. (The node
    # ticks far faster than detections arrive; fabricating empty scans here once
    # coasted every track to deletion before it could confirm.)
    _, _, publisher, published = _wire()
    assert publisher.tick() == []
    assert publisher.tick() == []
    assert published == []


def test_tracks_confirm_despite_many_idle_ticks_between_scans() -> None:
    # Regression for the "tracks[0] forever" bug: the node fires ~10 idle ticks
    # between 1 Hz scans. Those empty ticks must not prevent confirmation.
    _, radar, publisher, _ = _wire()
    last: list[Track] = []
    for k in range(1, 11):
        for _ in range(9):  # idle ticks while waiting for the next scan
            assert publisher.tick() == []  # buffer empty -> no-op
        radar.scan(T0 + timedelta(seconds=k))  # the scan's detections arrive
        last = publisher.tick()  # processed on the next tick
    assert len(last) == 3
