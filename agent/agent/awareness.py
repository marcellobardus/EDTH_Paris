"""
Local awareness picture (FR-7.3, FR-8.1). Pure, ROS-free, headless-testable.

Each agent builds its OWN map of {track -> interceptors assigned} from peer
state broadcasts. It is a *local* picture, not ground truth — that's the whole
point of Situation B, and why packet loss makes pictures diverge then
reconverge.

Decisions baked in:
  - Staleness (Q2, conservative): a peer silent < staleness_timeout_s still
    counts as covering its last-known track. Only an explicit alive=False or a
    peer Commit frees coverage. Silence alone never does — this minimises false
    conflicts under packet loss. `is_stale`/`stale_peers` are exposed for
    diagnostics but deliberately do NOT remove coverage.
  - Coverage conflict (architecture §4, conjunctive): an active track has 0
    interceptors assigned AND some interceptor (self included) is wasted —
    assigned to a track with >=2 interceptors OR to a track already dead. The
    conjunction avoids firing when there is no spare resource to fill the gap.

An interceptor that kills its target keeps its assignment pointing at the
(now-dead) track until it re-tasks; that "assigned to a dead track" is exactly
the wasted-resource signal the conflict predicate keys on.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.messages import Commit, EngagementEvent, InterceptorState, Track

Vec3 = tuple[float, float, float]


@dataclass
class PeerRecord:
    interceptor_id: str
    assigned_track_id: str | None
    alive: bool
    last_seen: float
    position: Vec3 = (0.0, 0.0, 0.0)
    velocity: Vec3 = (0.0, 0.0, 0.0)


class AwarenessPicture:
    def __init__(self, self_id: str, staleness_timeout_s: float) -> None:
        self.self_id = self_id
        self.timeout = staleness_timeout_s
        self._records: dict[str, PeerRecord] = {}
        self._tracks: dict[str, Track] = {}
        self._dead: set[str] = set()

    # -- ingest ------------------------------------------------------------
    def update_self(self, assigned_track_id: str | None, alive: bool, timestamp: float) -> None:
        self._records[self.self_id] = PeerRecord(self.self_id, assigned_track_id, alive, timestamp)

    def on_peer_state(self, st: InterceptorState) -> None:
        if st.interceptor_id == self.self_id:
            return  # ignore the echo of our own broadcast
        self._records[st.interceptor_id] = PeerRecord(
            st.interceptor_id,
            st.assigned_track_id,
            st.alive,
            st.timestamp,
            st.position,
            st.velocity,
        )

    def on_commit(self, commit: Commit) -> None:
        # FR-8.5: a peer Commit updates the local picture immediately, even if
        # we have not yet received that peer's next state broadcast.
        rec = self._records.get(commit.interceptor_id)
        if rec is not None:
            rec.assigned_track_id = commit.target_track_id
            rec.last_seen = commit.timestamp
        else:
            self._records[commit.interceptor_id] = PeerRecord(
                commit.interceptor_id, commit.target_track_id, True, commit.timestamp
            )

    def on_tracks(self, tracks: list[Track]) -> None:
        self._tracks = {t.track_id: t for t in tracks}
        for t in tracks:
            if not t.alive:
                self._dead.add(t.track_id)

    def on_engagement(self, event: EngagementEvent) -> None:
        if event.success:
            self._dead.add(event.track_id)

    # -- queries -----------------------------------------------------------
    def is_dead(self, track_id: str) -> bool:
        if track_id in self._dead:
            return True
        track = self._tracks.get(track_id)
        return track is not None and not track.alive

    def active_tracks(self) -> set[str]:
        return {tid for tid, t in self._tracks.items() if t.alive and tid not in self._dead}

    def is_stale(self, interceptor_id: str, now: float) -> bool:
        rec = self._records.get(interceptor_id)
        return rec is not None and (now - rec.last_seen) > self.timeout

    def stale_peers(self, now: float) -> set[str]:
        return {pid for pid in self._records if self.is_stale(pid, now)}

    def coverage(self) -> dict[str, list[str]]:
        """track_id -> interceptors assigned to it (alive interceptors only).

        Staleness does not remove coverage (conservative Q2). A dead/expended
        interceptor (alive=False) contributes nothing, so its track goes
        uncovered — which is what triggers re-tasking.
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
        """Architecture §4 conjunctive predicate."""
        if not self.uncovered_active_tracks():
            return False
        cov = self.coverage()
        return any(self._is_wasted(rec, cov) for rec in self._records.values())

    def _is_wasted(self, rec: PeerRecord, cov: dict[str, list[str]]) -> bool:
        track = rec.assigned_track_id
        if not rec.alive or track is None:
            return False
        return len(cov.get(track, [])) >= 2 or self.is_dead(track)
