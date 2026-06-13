"""
Decentralised re-tasking — claim-and-confirm protocol (A5, FR-8).

Pure and ROS-free, exactly like awareness/guidance/local_state: the node
(interceptor_agent.py) owns the Comms seam and feeds this FSM peer claims/commits
plus a periodic `tick(now)`. Every side effect leaves through two injected
callbacks (`emit_claim`, `emit_commit`) and through mutating the shared
`InterceptorLocalState` / `AwarenessPicture` — so the whole protocol is unit-
testable headless, with a hand-wired in-memory mesh standing in for DDS.

PROTOCOL (architecture §4 flowchart, FR-8.3-8.6)
------------------------------------------------
    IDLE --conflict & I'm a spare--> CLAIM(best uncovered T) --> WAIT(~400 ms)
      - a peer with HIGHER interceptor_id claims the same T  -> yield:
          round+1; pick next-best uncovered & re-CLAIM, or after max rounds
          fall back to greedy (closest uncovered) and COMMIT directly.
      - otherwise -> COMMIT(T): take the assignment, update the picture, and
          return to IDLE (guidance re-points itself off the new target()).

ARBITRATION IS BY interceptor_id, HIGHEST WINS (FR-8.4 / Claim contract /
architecture §4 "Higher-ID claim received for T?"). There is no score field on
Claim. Ids follow the i1..iN convention, so we compare on the trailing integer
(i10 > i2, not the lexical "i10" < "i2"); ids without a numeric suffix fall back
to a lexical tie-break.

KEY INVARIANTS (the traps the demo dies on)
-------------------------------------------
* Non-blocking: the 400 ms window is a deadline re-read at each tick, never a
  sleep — the ROS executor is single-threaded.
* Only a *spare* interceptor re-tasks (free / redundant on a double-covered
  track / sitting on a dead one). One uniquely covering a live track keeps it,
  so re-tasking fills the gap instead of merely shifting it.
* Same selection metric everywhere (closest uncovered, track_id tie-break) so an
  agent's claim target matches what its peers expect it to pick — deterministic
  per local picture.
* Idempotent under loss: a dropped COMMIT just means peers re-detect the
  conflict next cycle and re-resolve; committing is safe to repeat.
* No phantom commits: an expended interceptor (alive=False) or a target that
  dies mid-window abandons cleanly without committing.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from enum import Enum, auto

from contracts.messages import Claim, Commit

from agent.awareness import AwarenessPicture
from agent.local_state import InterceptorLocalState

Vec3 = tuple[float, float, float]


def _id_priority(interceptor_id: str) -> tuple[int, str]:
    """Total order for arbitration; higher tuple wins. Numeric suffix first
    (so i10 > i2), then the raw id as a lexical tie-break."""
    digits = ""
    for ch in reversed(interceptor_id):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    return (int(digits) if digits else -1, interceptor_id)


def _distance(a: Vec3, b: Vec3) -> float:
    return math.dist(a, b)


class _Phase(Enum):
    IDLE = auto()
    WAITING = auto()


class RetaskingProtocol:
    """Claim-and-confirm state machine for one interceptor."""

    def __init__(
        self,
        state: InterceptorLocalState,
        picture: AwarenessPicture,
        *,
        consensus_window_s: float,
        max_claim_rounds: int,
        emit_claim: Callable[[Claim], None],
        emit_commit: Callable[[Commit], None],
    ) -> None:
        self.state = state
        self.picture = picture
        self.window = consensus_window_s
        self.max_rounds = max_claim_rounds
        self._emit_claim = emit_claim
        self._emit_commit = emit_commit

        self._phase = _Phase.IDLE
        self._target: str | None = None       # track currently being claimed
        self._deadline = 0.0                   # wall-free time the window expires
        self._round = 0
        self._yielded: set[str] = set()        # targets ceded this episode
        self._lost_target = False              # a higher-id peer claimed _target

    # -- peer events (wired to lossy claim/commit subscriptions) -----------
    def on_peer_claim(self, claim: Claim) -> None:
        if claim.interceptor_id == self.state.id:
            return  # ignore our own echo
        if (
            self._phase is _Phase.WAITING
            and claim.target_track_id == self._target
            and _id_priority(claim.interceptor_id) > _id_priority(self.state.id)
        ):
            self._lost_target = True  # a higher-id peer outranks us on this target

    def on_peer_commit(self, commit: Commit) -> None:
        # FR-8.5: a peer Commit updates the local picture immediately.
        self.picture.on_commit(commit)
        if commit.interceptor_id == self.state.id:
            return
        # A peer locked the target we were chasing -> drop it and re-evaluate.
        if self._phase is _Phase.WAITING and commit.target_track_id == self._target:
            self._reset()

    # -- periodic driver ---------------------------------------------------
    def tick(self, now: float) -> None:
        if not self.state.alive:
            self._reset()  # expended: never claim or commit (no phantoms)
            return

        if self._phase is _Phase.WAITING:
            if now >= self._deadline:
                self._resolve(now)
            return

        # IDLE: start a new episode only if there is a real gap AND I'm the
        # spare expected to fill it (architecture §4 conjunctive predicate).
        if not self.picture.has_coverage_conflict() or not self._am_i_spare():
            return
        target = self._select_target()
        if target is None:
            return
        self._round = 0
        self._yielded = set()
        self._begin_claim(target, now)

    # -- internals ---------------------------------------------------------
    def _am_i_spare(self) -> bool:
        """True if re-tasking me does not strand a uniquely-covered live track:
        I'm free, redundant on a double-covered track, or sitting on a dead one."""
        tid = self.state.assigned_track_id
        if tid is None or self.picture.is_dead(tid):
            return True
        others = [c for c in self.picture.coverage().get(tid, []) if c != self.state.id]
        return len(others) >= 1

    def _select_target(self) -> str | None:
        """Best uncovered active track: closest to us, track_id as tie-break.
        Same metric peers assume of us, so claims line up deterministically."""
        candidates = self.picture.uncovered_active_tracks() - self._yielded
        if not candidates:
            return None
        return min(candidates, key=self._rank_key)

    def _rank_key(self, track_id: str) -> tuple[float, str]:
        track = self.state.latest_tracks.get(track_id)
        if track is None:
            return (math.inf, track_id)
        return (_distance(self.state.position, track.position), track_id)

    def _begin_claim(self, target: str, now: float) -> None:
        self._phase = _Phase.WAITING
        self._target = target
        self._deadline = now + self.window
        self._lost_target = False
        self._emit_claim(Claim(self.state.id, target, now))

    def _resolve(self, now: float) -> None:
        target = self._target
        assert target is not None  # WAITING always has a target

        # Target died inside the window -> abandon cleanly, no commit on a corpse.
        if self.picture.is_dead(target) or target not in self.picture.active_tracks():
            self._reset()
            return

        if not self._lost_target:
            self._commit(target, now)
            return

        # We were outranked: cede this target and try the next-best one.
        self._yielded.add(target)
        self._round += 1
        if self._round >= self.max_rounds:
            self._greedy_commit(now)  # FR-8.6 fallback under sustained contention/loss
            return
        nxt = self._select_target()
        if nxt is None:
            self._reset()  # nothing left to take; stand down
            return
        self._begin_claim(nxt, now)

    def _greedy_commit(self, now: float) -> None:
        # Closest uncovered active track, committed without another wait round.
        candidates = self.picture.uncovered_active_tracks()
        if not candidates:
            self._reset()
            return
        self._commit(min(candidates, key=self._rank_key), now)

    def _commit(self, target: str, now: float) -> None:
        # Take the assignment, reflect it locally at once (FR-8.5), broadcast.
        self.state.assigned_track_id = target
        self.picture.update_self(target, self.state.alive, now)
        self._emit_commit(Commit(self.state.id, target, now))
        self._reset()

    def _reset(self) -> None:
        self._phase = _Phase.IDLE
        self._target = None
        self._deadline = 0.0
        self._round = 0
        self._yielded = set()
        self._lost_target = False
