"""
Local awareness picture (FR-7.3, FR-8.1). Pure, ROS-free, headless-testable.

Each agent builds its OWN map of peers + tracks from the single CBAA
InterceptorState broadcast. It is a *local* picture, not ground truth — that's
the whole point of Situation B, and why packet loss makes pictures diverge then
reconverge.

Decisions baked in for CBAA:
  - Anti-replay (`seq`): each sender stamps a monotone `seq`; an out-of-order or
    replayed message (seq < what we already have) is dropped, so a late duplicate
    can't resurrect a stale ownership/lock.
  - Liveness (cause 1): `expire_silent` flips a peer to alive=False once it has
    been silent past `silence_timeout` — that DOES free its coverage. A fresh
    broadcast (higher seq) resurrects it, so a merely-lossy peer recovers.
  - Frozen threat (`threat_score`): a track's danger weight is computed once, at
    first sighting, and never moved — keeping every agent's priority key stable
    and (since all see the same GS broadcast) mutually consistent.
  - `owns_priority` is stored verbatim from the wire (normalised list->tuple) and
    NEVER recomputed here: peers arbitrate on the owner's own number.

The legacy coverage()/has_coverage_conflict() queries are retained for the
awareness logger; the CBAA protocol drives off peers()/tracks() directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from contracts.messages import EngagementEvent, InterceptorState, Track

Vec3 = tuple[float, float, float]
Key = tuple[float, float, float]


def compute_threat_score(position: Vec3, velocity: Vec3, protected_point: Vec3) -> float:
    """Danger weight of a track, higher = more dangerous. Approaching fast and
    close to the protected point scores near 1; receding scores by proximity
    alone. Frozen at first sighting so the priority key never drifts."""
    to_pp = (
        protected_point[0] - position[0],
        protected_point[1] - position[1],
        protected_point[2] - position[2],
    )
    dist = math.sqrt(to_pp[0] ** 2 + to_pp[1] ** 2 + to_pp[2] ** 2)
    if dist < 1e-9:
        return 1.0
    u = (to_pp[0] / dist, to_pp[1] / dist, to_pp[2] / dist)
    closing = velocity[0] * u[0] + velocity[1] * u[1] + velocity[2] * u[2]
    if closing <= 0.0:  # not approaching: rank by raw proximity only
        return 1.0 / (1.0 + dist)
    eta = dist / closing
    return 1.0 / (1.0 + eta)


@dataclass
class PeerRecord:
    interceptor_id: str
    assigned_track_id: str | None  # the track this peer `owns`, None if free
    alive: bool
    last_seen: float
    position: Vec3 = (0.0, 0.0, 0.0)
    velocity: Vec3 = (0.0, 0.0, 0.0)
    owns_priority: Key | None = None  # owner-computed key for assigned_track_id
    locked: bool = False
    seq: int = 0


class AwarenessPicture:
    def __init__(
        self,
        self_id: str,
        staleness_timeout_s: float,
        protected_point: Vec3 = (0.0, 0.0, 0.0),
    ) -> None:
        self.self_id = self_id
        self.timeout = staleness_timeout_s
        self.protected_point = protected_point
        self._records: dict[str, PeerRecord] = {}
        self._tracks: dict[str, Track] = {}
        self._dead: set[str] = set()
        self._threat: dict[str, float] = {}  # frozen at first sighting

    # -- ingest ------------------------------------------------------------
    def update_self(self, assigned_track_id: str | None, alive: bool, timestamp: float) -> None:
        self._records[self.self_id] = PeerRecord(self.self_id, assigned_track_id, alive, timestamp)

    def on_peer_state(self, st: InterceptorState) -> None:
        if st.interceptor_id == self.self_id:
            return  # ignore the echo of our own broadcast
        prev = self._records.get(st.interceptor_id)
        if prev is not None and st.seq < prev.seq:
            return  # anti-replay: an older message can't undo newer state
        self._records[st.interceptor_id] = PeerRecord(
            st.interceptor_id,
            st.assigned_track_id,
            st.alive,
            st.timestamp,
            st.position,
            st.velocity,
            # JSON has no tuple type, so owns_priority arrives as a list; the
            # priority comparisons need a tuple (list vs tuple is unorderable).
            tuple(st.owns_priority) if st.owns_priority is not None else None,  # type: ignore[arg-type]
            st.locked,
            st.seq,
        )

    def on_tracks(self, tracks: list[Track]) -> None:
        self._tracks = {t.track_id: t for t in tracks}
        for t in tracks:
            if not t.alive:
                self._dead.add(t.track_id)
            if t.track_id not in self._threat:  # freeze threat on first sighting
                self._threat[t.track_id] = compute_threat_score(
                    t.position, t.velocity, self.protected_point
                )

    def on_engagement(self, event: EngagementEvent) -> None:
        if event.success:
            self._dead.add(event.track_id)

    def expire_silent(self, now: float, silence_timeout: float) -> None:
        """Liveness cause 1: mark peers silent past `silence_timeout` as dead so
        their coverage frees. Self is never expired (we update it every cycle)."""
        for pid, rec in self._records.items():
            if pid == self.self_id or not rec.alive:
                continue
            if now - rec.last_seen > silence_timeout:
                rec.alive = False

    # -- queries -----------------------------------------------------------
    def is_dead(self, track_id: str) -> bool:
        if track_id in self._dead:
            return True
        track = self._tracks.get(track_id)
        return track is not None and not track.alive

    def active_tracks(self) -> set[str]:
        return {tid for tid, t in self._tracks.items() if t.alive and tid not in self._dead}

    def track(self, track_id: str) -> Track | None:
        return self._tracks.get(track_id)

    def tracks(self) -> dict[str, Track]:
        return self._tracks

    def threat_score(self, track_id: str) -> float:
        return self._threat.get(track_id, 0.0)

    def peers(self) -> list[PeerRecord]:
        """Every peer EXCEPT ourselves — the CBAA arbitration set."""
        return [rec for pid, rec in self._records.items() if pid != self.self_id]

    def is_stale(self, interceptor_id: str, now: float) -> bool:
        rec = self._records.get(interceptor_id)
        return rec is not None and (now - rec.last_seen) > self.timeout

    def stale_peers(self, now: float) -> set[str]:
        return {pid for pid in self._records if self.is_stale(pid, now)}

    def coverage(self) -> dict[str, list[str]]:
        """track_id -> interceptors owning it (alive interceptors only).

        A dead/expended interceptor (alive=False) contributes nothing, so its
        track goes uncovered — which is what triggers re-tasking.
        """
        cov: dict[str, list[str]] = {}
        for rec in self._records.values():
            if rec.alive and rec.assigned_track_id is not None:
                cov.setdefault(rec.assigned_track_id, []).append(rec.interceptor_id)
        return cov

    def uncovered_active_tracks(self) -> set[str]:
        cov = self.coverage()
        return {tid for tid in self.active_tracks() if not cov.get(tid)}

    def has_coverage_conflict(self) -> bool:
        """Architecture §4 conjunctive predicate (diagnostic logger only)."""
        if not self.uncovered_active_tracks():
            return False
        cov = self.coverage()
        return any(self._is_wasted(rec, cov) for rec in self._records.values())

    def _is_wasted(self, rec: PeerRecord, cov: dict[str, list[str]]) -> bool:
        track = rec.assigned_track_id
        if not rec.alive or track is None:
            return False
        return len(cov.get(track, [])) >= 2 or self.is_dead(track)
