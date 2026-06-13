"""
Bus <-> tracker bridge.

The radar publishes ``RadarDetection`` messages one at a time as it sees targets,
but the multi-target tracker wants a whole scan's detections together so it can
solve association jointly. :class:`TrackPublisher` reconciles the two: it buffers
incoming detections and, on each :meth:`tick`, fuses the accumulated batch and
publishes the resulting ``Track`` estimates on ``Topics.GS_TRACKS``.

Ticking is decoupled from arrival — the node calls :meth:`tick` on a fixed
cadence (e.g. 10 Hz) — so the tracker advances at a steady rate regardless of how
the radar bunches its detections, and keeps coasting/deleting tracks even when
the radar goes quiet.

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
    """Buffers radar detections and publishes fused tracks on a fixed cadence."""

    def __init__(
        self,
        bus: Bus,
        *,
        start_time: datetime,
        tick_interval_s: float = 0.1,
        on_tracks: Callable[[list[Track]], None] | None = None,
        **tracker_kwargs: Any,  # forwarded to MultiTargetTracker (the tuning knobs)
    ) -> None:
        self._bus = bus
        self._tracker = MultiTargetTracker(start_time=start_time, **tracker_kwargs)
        self._buffer: list[RadarDetection] = []
        self._clock: float | None = None  # latest scenario time processed (s)
        self._tick_interval = tick_interval_s
        self._on_tracks = on_tracks
        bus.subscribe(Topics.RADAR_DETECTIONS, RadarDetection, self._on_detection)

    def _on_detection(self, detection: RadarDetection) -> None:
        # Append via a method so the handler always targets the *current* buffer,
        # even after tick() swaps it out.
        self._buffer.append(detection)

    def tick(self) -> list[Track]:
        """Fuse the buffered detections and publish the resulting tracks.

        Returns the tracks published this tick (empty before the first
        detection). With detections, the tick is timestamped at the batch's
        latest detection; without, the clock advances by ``tick_interval_s`` so
        idle tracks still coast and get deleted.
        """
        batch = self._buffer
        self._buffer = []

        if batch:
            timestamp = max(det.timestamp for det in batch)
            if self._clock is not None:
                timestamp = max(timestamp, self._clock)  # never step backwards
        elif self._clock is not None:
            timestamp = self._clock + self._tick_interval
        else:
            return []  # nothing seen yet — nothing to advance

        tracks = self._tracker.process(batch, timestamp)
        self._clock = timestamp

        for track in tracks:
            self._bus.publish(Topics.GS_TRACKS, track)
        if self._on_tracks is not None:
            self._on_tracks(tracks)
        return tracks
