"""
Decentralised re-tasking — CBAA variant, stale-safe (A5, FR-8).

Pure and ROS-free, exactly like awareness/guidance/local_state: the node
(interceptor_agent.py) owns the Comms seam and feeds this engine peer state
(via the shared AwarenessPicture) plus a periodic `tick(now)`. The ONE side
effect leaves through a single injected callback `emit_state` — the protocol's
only message is the InterceptorState broadcast (the old Claim/Commit pair is
gone). So the whole protocol is unit-testable headless, with a hand-wired
in-memory mesh standing in for DDS.

WHY CBAA INSTEAD OF CLAIM-AND-CONFIRM
-------------------------------------
Claim-and-confirm needed a synchronous ~400 ms window and three message types;
under loss the window logic got fragile. CBAA collapses everything into one
idempotent, level-triggered broadcast: each interceptor advertises which track
it `owns`, the self-computed `owns_priority` that justifies it, and a monotone
`locked` flag. Ownership is resolved by a *total order on a priority key* — no
rounds, no deadline, no commit. Convergence rides on the periodic re-broadcast,
so a dropped packet just delays a decision by a cycle instead of breaking it.

THE PRIORITY KEY (total order, time-invariant by construction)
--------------------------------------------------------------
    Key = (affinity_bucket, danger, id_rank)        # lexicographic, LARGER wins
      affinity_bucket = frozen_threat / (intercept_bucket + 1)
      danger          = -distance(track, protected_point)   # tie-break 1
      id_rank         = numeric suffix of the id            # tie-break 2 (final)
The intercept time is *bucketed* (with hysteresis at the boundaries) so small
estimate jitter never reorders the key — that, plus an incumbency margin and a
monotone lock, is what keeps assignments from oscillating.

KEY INVARIANTS (the traps the demo dies on)
-------------------------------------------
* No recompute of a peer's key: peers compare the transmitted `owns_priority`.
* Monotone lock: once intercept_time < lock_threshold we lock and never yield —
  terminal guidance is not interruptible.
* Stale-safe: a silent peer keeps covering its last track until SILENCE_TIMEOUT
  (then awareness frees it); an old (lower-seq) packet is ignored.
* Idempotent under loss: a changed state is re-emitted CHANGE_REPEAT times and a
  heartbeat goes out every cycle, so peers reconverge after dropped packets.
* No phantom ownership on a corpse: a dead target or an expended self releases
  cleanly and broadcasts the release.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from contracts.messages import InterceptorState

from agent.awareness import AwarenessPicture
from agent.guidance import intercept_time
from agent.local_state import InterceptorLocalState

Vec3 = tuple[float, float, float]
Key = tuple[float, float, float]

_NEG_INF = float("-inf")


def _id_rank(interceptor_id: str) -> float:
    """Numeric suffix as a rank (higher wins); -1 if the id has no digits.
    The i1..iN convention orders on the trailing integer (i10 > i2)."""
    digits = ""
    for ch in reversed(interceptor_id):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    return float(int(digits)) if digits else -1.0


def _distance(a: Vec3, b: Vec3) -> float:
    return math.dist(a, b)


class RetaskingProtocol:
    """CBAA re-tasking engine for one interceptor."""

    def __init__(
        self,
        state: InterceptorLocalState,
        picture: AwarenessPicture,
        *,
        emit_state: Callable[[InterceptorState], None],
        range_m: float,
        speed_mps: float,
        protected_point: Vec3,
        lock_threshold_s: float,
        bucket_size_s: float,
        bucket_hysteresis_s: float,
        incumbency_margin: float,
        change_repeat: int,
        heartbeat_period_s: float,
        silence_timeout_s: float,
    ) -> None:
        self.state = state
        self.picture = picture
        self._emit_state = emit_state
        self.range = range_m
        self.speed = speed_mps
        self.protected_point = protected_point
        self.lock_threshold = lock_threshold_s
        self.bucket_size = bucket_size_s
        self.bucket_hysteresis = bucket_hysteresis_s
        self.incumbency_margin = incumbency_margin
        self.change_repeat = change_repeat
        self.heartbeat_period = heartbeat_period_s
        self.silence_timeout = silence_timeout_s

        self._id_rank = _id_rank(state.id)
        # `owns` is mirrored on state.assigned_track_id so guidance re-points off
        # it; the key/bucket/lock that justify it live here.
        self.owns_priority: Key | None = None
        self.owns_bucket: int | None = None
        self.locked = False

        self._seq = 0
        self._repeats_left = 0
        # last broadcast snapshot, for change detection / heartbeat scheduling.
        self._last_owns: str | None = None
        self._last_locked = False
        self._last_alive = True
        self._last_time = _NEG_INF

    # -- properties --------------------------------------------------------
    @property
    def owns(self) -> str | None:
        return self.state.assigned_track_id

    # -- periodic driver (DECISION_PERIOD, 5 Hz) ---------------------------
    def tick(self, now: float) -> None:
        self.picture.expire_silent(now, self.silence_timeout)  # liveness cause 1

        if not self.state.alive:
            # Cause 2: expended -> drop ownership and tell peers (alive=False).
            if self.owns is not None:
                self.release()
            self.force_emit(now)
            return

        if self.locked:
            self.maybe_emit(now)  # monotone: hold, just keep broadcasting
            return

        self.update_lock()
        if self.locked:
            self.force_emit(now)  # announce the lock immediately
            return

        self.resolve_assignment()
        self.maybe_emit(now)

    # -- core: validate / yield / acquire ----------------------------------
    def resolve_assignment(self) -> None:
        # (A) Cause 2: is the owned target dead?
        if self.owns is not None:
            t = self.picture.track(self.owns)
            if t is None or not t.alive or self.picture.is_dead(self.owns):
                self.release()

        # (B) Cause 3: a converging conflict we should cede?
        if self.owns is not None:
            self.refresh_owns_priority()  # compare on a current key
            if self.should_yield():
                self.release()

        # (C) Free -> acquire the best available target.
        if self.owns is None:
            target = self.best_available_target()
            if target is not None:
                self.acquire(target)

    def should_yield(self) -> bool:
        mine = self.owns_priority
        if mine is None:
            return False
        rivals = [
            p.owns_priority
            for p in self.picture.peers()
            if p.alive
            and not p.locked
            and p.assigned_track_id == self.owns
            and p.owns_priority is not None
        ]
        if not rivals:
            return False
        return max(rivals) > mine  # strict; id_rank rules out exact ties

    def best_available_target(self) -> str | None:
        best_track: str | None = None
        best_key: Key | None = None
        for tid, t in self.picture.tracks().items():
            if not t.alive or self.picture.is_dead(tid) or not self.can_take(t):
                continue
            k = self.priority_key(t, None)
            if best_key is None or k > best_key:
                best_key, best_track = k, tid
        return best_track

    def can_take(self, track: object) -> bool:
        tid = track.track_id  # type: ignore[attr-defined]
        k = self.priority_key(track, None)
        if k[0] == _NEG_INF:  # out of range / uncatchable
            return False
        peers = self.picture.peers()
        if any(p.locked and p.assigned_track_id == tid for p in peers):
            return False  # someone has terminal lock on it
        incumbent_keys = [
            p.owns_priority
            for p in peers
            if p.alive
            and not p.locked
            and p.assigned_track_id == tid
            and p.owns_priority is not None
        ]
        if not incumbent_keys:
            return True  # uncovered -> free to take
        return self._beats_clearly(k, max(incumbent_keys))  # must beat the holder by margin

    def _beats_clearly(self, challenger: Key, incumbent: Key) -> bool:
        """A challenger displaces an incumbent only by beating its PRIMARY
        component by `incumbency_margin` (ties on a near-equal bucket stay with
        the holder — stickiness)."""
        return challenger > (incumbent[0] + self.incumbency_margin, incumbent[1], incumbent[2])

    # -- priority key ------------------------------------------------------
    def priority_key(self, track: object, prev_bucket: int | None) -> Key:
        pos = track.position  # type: ignore[attr-defined]
        if _distance(self.state.position, pos) > self.range:
            return (_NEG_INF, 0.0, self._id_rank)  # out of range -> excluded
        t_icpt = intercept_time(self.state.position, self.speed, pos, track.velocity)  # type: ignore[attr-defined]
        if not math.isfinite(t_icpt):
            return (_NEG_INF, 0.0, self._id_rank)  # uncatchable -> excluded
        b = self._bucket(t_icpt, prev_bucket)
        affinity = self.picture.threat_score(track.track_id) / (b + 1)  # type: ignore[attr-defined]
        danger = -_distance(pos, self.protected_point)
        return (affinity, danger, self._id_rank)

    def _bucket(self, t: float, prev_bucket: int | None) -> int:
        """intercept_time quantised, with hysteresis so jitter at a boundary
        does not flip the bucket (and thus the key)."""
        if not math.isfinite(t):  # uncatchable -> a very high (low-priority) bucket
            return 10**9
        raw = int(math.floor(t / self.bucket_size))
        if prev_bucket is None:
            return raw
        lower = prev_bucket * self.bucket_size
        upper = (prev_bucket + 1) * self.bucket_size
        if t < lower - self.bucket_hysteresis:
            return raw  # dropped clearly below
        if t >= upper + self.bucket_hysteresis:
            return raw  # rose clearly above
        return prev_bucket  # inside the sticky band -> stay

    # -- acquire / release / refresh ---------------------------------------
    def acquire(self, track_id: str) -> None:
        t = self.picture.track(track_id)
        assert t is not None  # only called for a track we just selected
        self.state.assigned_track_id = track_id
        t_icpt = intercept_time(self.state.position, self.speed, t.position, t.velocity)
        self.owns_bucket = self._bucket(t_icpt, None)
        self.owns_priority = self.priority_key(t, self.owns_bucket)
        self._repeats_left = self.change_repeat

    def release(self) -> None:
        self.state.assigned_track_id = None
        self.owns_priority = None
        self.owns_bucket = None
        self._repeats_left = self.change_repeat

    def refresh_owns_priority(self) -> None:
        if self.owns is None:
            return
        t = self.picture.track(self.owns)
        if t is None:
            return
        t_icpt = intercept_time(self.state.position, self.speed, t.position, t.velocity)
        self.owns_bucket = self._bucket(t_icpt, self.owns_bucket)
        self.owns_priority = self.priority_key(t, self.owns_bucket)

    # -- lock (monotone, self-declared) ------------------------------------
    def update_lock(self) -> None:
        if self.locked or self.owns is None:
            return
        t = self.picture.track(self.owns)
        if t is None or not t.alive or self.picture.is_dead(self.owns):
            return
        t_icpt = intercept_time(self.state.position, self.speed, t.position, t.velocity)
        if t_icpt < self.lock_threshold:
            self.locked = True

    # -- communication (event-driven + loss robustness) --------------------
    def maybe_emit(self, now: float) -> None:
        self.refresh_owns_priority()
        changed = (
            self.owns != self._last_owns
            or self.locked != self._last_locked
            or self.state.alive != self._last_alive
        )
        heartbeat_due = (now - self._last_time) >= self.heartbeat_period
        if changed:
            self._repeats_left = self.change_repeat  # re-emit x3 (0.1^3 = 0.1% miss)
        if changed or heartbeat_due or self._repeats_left > 0:
            self._broadcast(now)
            self._repeats_left = max(0, self._repeats_left - 1)

    def force_emit(self, now: float) -> None:
        self._repeats_left = self.change_repeat
        self._broadcast(now)

    def _broadcast(self, now: float) -> None:
        self._seq += 1
        # Keep our own entry in the picture current (the awareness logger reads it).
        self.picture.update_self(self.owns, self.state.alive, now)
        msg = InterceptorState(
            interceptor_id=self.state.id,
            position=self.state.position,
            velocity=self.state.velocity,
            assigned_track_id=self.owns,
            alive=self.state.alive,
            timestamp=now,
            owns_priority=self.owns_priority,
            locked=self.locked,
            seq=self._seq,
        )
        self._emit_state(msg)
        self._last_owns = self.owns
        self._last_locked = self.locked
        self._last_alive = self.state.alive
        self._last_time = now
