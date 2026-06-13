"""Integration: Stone-Soup radar -> multi-target tracker fused tracks (Phase 3)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
from agent.bus import MockBroker
from contracts.messages import RadarDetection
from gs.tracker import MultiTargetTracker
from sim.radar_stonesoup import StoneSoupRadar, TargetInit

T0 = datetime(2026, 6, 13, 12, 0, 0)

# Three well-separated Shaheds closing on the origin from far out, at 100 m alt.
_THREE = [
    TargetInit("north", (0.0, 0.0, 3000.0, -50.0, 100.0, 0.0)),
    TargetInit("east", (3000.0, -50.0, 0.0, 0.0, 100.0, 0.0)),
    TargetInit("south", (0.0, 0.0, -3000.0, 50.0, 100.0, 0.0)),
]


def _radar(targets: list[TargetInit], **kw: float) -> StoneSoupRadar:
    # Radar still needs a bus to publish to; nobody listens — we read scan()'s
    # returned hits directly, which is how the node will feed the tracker.
    bus = MockBroker().endpoint("radar1")
    return StoneSoupRadar(bus, "radar1", targets, start_time=T0, seed=1, **kw)


def test_three_targets_yield_three_stable_tracks() -> None:
    radar = _radar(_THREE, prob_detect=1.0)
    tracker = MultiTargetTracker(start_time=T0)

    counts: list[int] = []
    for k in range(1, 16):
        dets = radar.scan(T0 + timedelta(seconds=k))
        tracks = tracker.process(dets, float(k))
        counts.append(len(tracks))

    assert counts[-1] == 3  # settles on exactly three
    assert all(c == 3 for c in counts[5:])  # and stays there after warm-up


def test_track_ids_persist_across_scans() -> None:
    radar = _radar(_THREE, prob_detect=1.0)
    tracker = MultiTargetTracker(start_time=T0)

    id_sets: list[frozenset[str]] = []
    for k in range(1, 16):
        dets = radar.scan(T0 + timedelta(seconds=k))
        tracks = tracker.process(dets, float(k))
        id_sets.append(frozenset(t.track_id for t in tracks))

    # Once confirmed, the same three IDs persist (no churn / re-initiation).
    assert len(id_sets[-1]) == 3
    assert id_sets[-1] == id_sets[-2] == id_sets[-3]


def test_clutter_does_not_create_confirmed_tracks() -> None:
    radar = _radar(_THREE, prob_detect=1.0)
    tracker = MultiTargetTracker(start_time=T0)
    clutter_rng = np.random.default_rng(99)

    counts: list[int] = []
    for k in range(1, 16):
        dets = radar.scan(T0 + timedelta(seconds=k))
        # One-off clutter each scan at fresh random positions: M-of-N (min 2)
        # should stop any of it from confirming into a track.
        for _ in range(3):
            p = clutter_rng.uniform(-4000, 4000, size=3)
            dets.append(RadarDetection("clutter", (p[0], p[1], 100.0), float(k)))
        counts.append(len(tracker.process(dets, float(k))))

    assert all(c == 3 for c in counts[6:])  # only the three real targets confirm


def test_stale_track_is_deleted_when_detections_stop() -> None:
    radar = _radar([_THREE[0]], prob_detect=1.0)
    tracker = MultiTargetTracker(start_time=T0)

    for k in range(1, 8):  # establish one confirmed track
        tracker.process(radar.scan(T0 + timedelta(seconds=k)), float(k))
    assert len(tracker.tracks) == 1

    # Detections stop; the coasting track's covariance grows past threshold.
    survived: list[int] = []
    for k in range(8, 20):
        survived.append(len(tracker.process([], float(k))))
    assert survived[0] == 1  # survives the first few misses (coasts)
    assert survived[-1] == 0  # eventually deleted


def test_fused_position_tracks_ground_truth() -> None:
    # process_noise=0 makes the truth a clean straight line, so we know it
    # analytically: x = -3000 + 50 t, y = 0, z = 100.
    target = TargetInit("straight", (-3000.0, 50.0, 0.0, 0.0, 100.0, 0.0))
    radar = _radar([target], prob_detect=1.0, process_noise=0.0, position_noise_m=5.0)
    tracker = MultiTargetTracker(start_time=T0, process_noise=1.0)

    tracks = []
    for k in range(1, 16):
        tracks = tracker.process(radar.scan(T0 + timedelta(seconds=k)), float(k))
    assert len(tracks) == 1

    t = tracks[0]
    truth = np.array([-3000.0 + 50.0 * 15, 0.0, 100.0])
    err = np.linalg.norm(np.array(t.position) - truth)
    assert err < 10.0  # within a few metres of truth
    assert abs(t.velocity[0] - 50.0) < 8.0  # x-velocity recovered
    assert t.alive is True
    assert len(t.covariance) == 6 and len(t.covariance[0]) == 6
