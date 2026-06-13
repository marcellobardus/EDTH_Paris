"""
Multi-target tracker (track fusion).

Fuses the noisy ``RadarDetection`` stream from one or more radars into a set of
clean, identity-stable ``Track`` estimates — the ground station's view of every
Shahed currently in the air.

Built from Stone Soup components, but driven by a **manual** scan loop rather
than Stone Soup's ``MultiTargetTracker`` (which pulls from an iterator). The repo
bus is push-based — detections arrive in callbacks — so the tracker exposes a
single :meth:`process` call that the node ticks per scan. The loop itself is the
canonical Stone Soup sequence (associate -> update / coast -> delete -> initiate):

1. associate live tracks to this tick's detections (GNN over a Mahalanobis gate);
2. update matched tracks with their detection; coast unmatched tracks on the
   prediction alone (covariance grows);
3. delete tracks whose covariance has grown past a threshold (sustained misses);
4. initiate new tracks from leftover detections via M-of-N confirmation.

The predictor / updater / measurement model all come from a
:class:`~gs.filter.SingleTargetFilter`, so the motion and sensor models match
both the single-target filter and the radar simulator.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
from contracts.messages import RadarDetection, Track
from stonesoup.dataassociator.neighbour import GNNWith2DAssignment
from stonesoup.deleter.error import CovarianceBasedDeleter
from stonesoup.hypothesiser.distance import DistanceHypothesiser
from stonesoup.initiator.simple import MultiMeasurementInitiator
from stonesoup.measures import Mahalanobis
from stonesoup.types.array import StateVector
from stonesoup.types.detection import Detection
from stonesoup.types.state import GaussianState
from stonesoup.types.track import Track as StoneSoupTrack

from gs.filter import SingleTargetFilter


class MultiTargetTracker:
    """Turns per-scan radar detections into a live set of fused tracks.

    Tuning knobs (all with hackathon-reasonable defaults):

    - ``gate_distance`` — Mahalanobis gate radius; detections beyond this from a
      track's prediction cannot associate to it. Defaults to 4.0 rather than the
      Stone Soup tutorial's 3.0: at our 3-D position noise a 3σ gate occasionally
      rejects a genuine detection, which spawns a short-lived duplicate track;
      4.0 closes that gap without admitting clutter.
    - ``min_init_points`` — M-of-N confirmation: detections must associate for
      this many scans before a tentative track is promoted to a real one
      (suppresses one-off clutter).
    - ``covar_trace_thresh`` — a coasting track is deleted once its covariance
      trace exceeds this (≈ 5-6 consecutive missed scans at the defaults).

    ``start_time`` anchors the float detection timestamps (seconds since
    scenario start) to the absolute ``datetime`` clock Stone Soup predicts on; it
    must match the radar's ``start_time``.
    """

    def __init__(
        self,
        *,
        start_time: datetime,
        process_noise: float = 1.0,
        measurement_noise_m: float = 5.0,
        initial_velocity_std_mps: float = 50.0,
        gate_distance: float = 4.0,
        min_init_points: int = 2,
        covar_trace_thresh: float = 1000.0,
    ) -> None:
        self._start_time = start_time
        self._filter = SingleTargetFilter(
            process_noise=process_noise,
            measurement_noise_m=measurement_noise_m,
            initial_velocity_std_mps=initial_velocity_std_mps,
        )
        hypothesiser = DistanceHypothesiser(
            self._filter.predictor,
            self._filter.updater,
            measure=Mahalanobis(),
            missed_distance=gate_distance,
        )
        self._associator = GNNWith2DAssignment(hypothesiser)
        self._deleter = CovarianceBasedDeleter(covar_trace_thresh=covar_trace_thresh)
        # Birth prior: position variance is overwritten from each measurement via
        # the measurement model; only the wide velocity prior matters here.
        birth_prior = GaussianState(
            np.zeros((6, 1)),
            np.diag([measurement_noise_m**2, initial_velocity_std_mps**2] * 3),
            timestamp=start_time,
        )
        self._initiator = MultiMeasurementInitiator(
            prior_state=birth_prior,
            deleter=self._deleter,
            data_associator=self._associator,
            updater=self._filter.updater,
            measurement_model=self._filter.measurement_model,
            min_points=min_init_points,
        )
        self._tracks: set[StoneSoupTrack] = set()

    @property
    def tracks(self) -> set[StoneSoupTrack]:
        """The live Stone Soup tracks (confirmed only; tentative tracks live
        inside the initiator until they reach ``min_init_points``)."""
        return self._tracks

    def process(self, detections: list[RadarDetection], timestamp: float) -> list[Track]:
        """Advance the tracker by one scan and return the current fused tracks.

        ``timestamp`` is seconds since scenario start (as carried on
        ``RadarDetection``). Detections from the same scan should be passed
        together so the multi-target association is solved jointly.
        """
        time = self._start_time + timedelta(seconds=timestamp)
        ss_detections = {self._to_detection(det, time) for det in detections}

        associations = self._associator.associate(self._tracks, ss_detections, time)
        associated: set[Detection] = set()
        for track, hypothesis in associations.items():
            if hypothesis:
                track.append(self._filter.updater.update(hypothesis))
                associated.add(hypothesis.measurement)
            else:
                track.append(hypothesis.prediction)  # coast on the prediction

        self._tracks -= self._deleter.delete_tracks(self._tracks)
        self._tracks |= self._initiator.initiate(ss_detections - associated, time)

        return [_to_contract_track(track, timestamp) for track in self._tracks]

    def _to_detection(self, detection: RadarDetection, time: datetime) -> Detection:
        """Wrap a ``RadarDetection`` position as a Stone Soup ``Detection``
        carrying the shared measurement model."""
        x, y, z = detection.position
        return Detection(
            StateVector([[x], [y], [z]]),
            timestamp=time,
            measurement_model=self._filter.measurement_model,
        )


def _to_contract_track(track: StoneSoupTrack, timestamp: float) -> Track:
    """Map a Stone Soup track to the wire ``Track`` contract.

    ``covariance`` is the full 6×6 state covariance in Stone Soup's native
    ``[x, vx, y, vy, z, vz]`` ordering — NOT ``[x, y, z, vx, vy, vz]``. Consumers
    that want position/velocity blocks should read the ``position`` / ``velocity``
    fields directly rather than slicing the covariance.
    """
    sv = track.state_vector
    x, vx, y, vy, z, vz = (float(sv[i, 0]) for i in range(6))
    covariance = [[float(v) for v in row] for row in np.asarray(track.covar)]
    return Track(
        track_id=str(track.id),
        position=(x, y, z),
        velocity=(vx, vy, vz),
        covariance=covariance,
        alive=True,
        timestamp=timestamp,
    )
