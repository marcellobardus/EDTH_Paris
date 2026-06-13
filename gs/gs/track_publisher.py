"""
Bus <-> tracker bridge.

The radar publishes ``RadarDetection`` messages one at a time as it sees targets,
but the multi-target tracker wants a whole scan's detections together so it can
solve association jointly. :class:`TrackPublisher` reconciles the two: it buffers
incoming detections and, on each :meth:`tick`, fuses the accumulated batch and
publishes the resulting ``Track`` estimates on ``Topics.GS_TRACKS``.

A *tick* corresponds to a radar scan, not a wall-clock instant: the node may tick
faster than detections arrive, so a tick with an empty buffer is a **no-op**. The
tracker is advanced only by real detections. (An earlier version coasted on every
empty tick; with detections sparser than the tick rate that fabricated phantom
"all targets missed" scans and deleted tracks before they could ever confirm.)
Coasting/deletion of a genuinely undetected target still happens correctly the
next time *any* scan arrives, because the tracker predicts each track forward to
that scan's timestamp.

The class holds no transport details beyond the ``Bus`` it is handed, so tests
drive it with a ``MockBroker`` and call :meth:`tick` directly (no timer).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from contracts.bus import Bus
from contracts.messages import RadarDetection, Track
from contracts.topics import Topics

from gs.tracker import MultiTargetTracker


class TrackPublisher:
    """Buffers radar detections and publishes fused tracks once per scan."""

    def __init__(
        self,
        bus: Bus,
        *,
        start_time: datetime,
        on_tracks: Callable[[list[Track], float], None] | None = None,
        **tracker_kwargs: Any,  # forwarded to MultiTargetTracker (the tuning knobs)
    ) -> None:
        self._bus = bus
        self._tracker = MultiTargetTracker(start_time=start_time, **tracker_kwargs)
        self._buffer: list[RadarDetection] = []
        self._clock: float | None = None  # latest scenario time processed (s)
        self._on_tracks = on_tracks
        bus.subscribe(Topics.RADAR_DETECTIONS, RadarDetection, self._on_detection)

    def _on_detection(self, detection: RadarDetection) -> None:
        # Append via a method so the handler always targets the *current* buffer,
        # even after tick() swaps it out.
        self._buffer.append(detection)

    def tick(self) -> list[Track]:
        """Fuse the detections buffered since the last tick and publish the
        resulting tracks. Returns the published tracks, or an empty list when no
        detections have arrived (a no-op tick — the tracker is not advanced)."""
        if not self._buffer:
            return []

        batch = self._buffer
        self._buffer = []
        timestamp = max(det.timestamp for det in batch)
        if self._clock is not None:
            timestamp = max(timestamp, self._clock)  # never step backwards

        tracks = self._tracker.process(batch, timestamp)
        self._clock = timestamp

        for track in tracks:
            self._bus.publish(Topics.GS_TRACKS, track)
        if self._on_tracks is not None:
            self._on_tracks(tracks, timestamp)
        return tracks
