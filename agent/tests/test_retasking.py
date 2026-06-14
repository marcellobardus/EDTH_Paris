"""A5 tests (FR-8): CBAA decentralised re-tasking, via an in-memory mesh.

The engine is pure (RetaskingProtocol talks only to AwarenessPicture /
InterceptorLocalState and one emit_state callback), so we exercise it headless
with a hand-wired mesh standing in for DDS — no ROS, no Docker.

Mesh model (the part that has to be faithful, or the tests lie):
  * State broadcasts are EVENTS with one tick of latency: a message emitted while
    ticking step N is delivered at the start of step N+1. That is what makes the
    decentralised pictures briefly diverge — exactly like async pub/sub.
  * There is only ONE message type now (InterceptorState): it carries ownership,
    the owner's priority key, and the lock flag. No claim/commit.
  * Loss is a seeded predicate over (msg, dst) so packet-loss sweeps are
    deterministic and per-link. Reconvergence rides on the periodic re-broadcast
    (heartbeat + CHANGE_REPEAT re-emits), since a dropped packet only delays a
    decision by a cycle.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from agent.awareness import AwarenessPicture
from agent.local_state import InterceptorLocalState
from agent.retasking import RetaskingProtocol
from contracts.messages import InterceptorState, Track

Vec3 = tuple[float, float, float]
DropFn = Callable[[InterceptorState, str], bool]
_NO_LOSS: DropFn = lambda msg, dst: False  # noqa: E731

PROTECTED = (200.0, 0.0, 0.0)


def _track(tid: str, pos: Vec3, *, alive: bool = True) -> Track:
    cov = [[0.0] * 6 for _ in range(6)]
    return Track(tid, pos, (0.0, 0.0, 0.0), cov, alive, 0.0)


class _Mesh:
    """Deferred, optionally-lossy state fabric for a set of agents."""

    def __init__(self, *, drop: DropFn = _NO_LOSS) -> None:
        self.agents: dict[str, _Agent] = {}
        self._drop = drop
        self._inflight: list[InterceptorState] = []  # emitted, not yet delivered
        self.states: list[InterceptorState] = []  # every emission, for assertions
        self.muted: set[str] = set()  # ids whose broadcasts are dropped entirely

    def register(self, agent: _Agent) -> None:
        self.agents[agent.id] = agent

    # emit callback handed to each RetaskingProtocol ----------------------
    def send_state(self, st: InterceptorState) -> None:
        self.states.append(st)
        if st.interceptor_id not in self.muted:
            self._inflight.append(st)

    # driven by the step loop --------------------------------------------
    def deliver(self) -> None:
        """Flush last step's broadcasts to every other agent (lossy)."""
        batch, self._inflight = self._inflight, []
        for msg in batch:
            for dst, ag in self.agents.items():
                if dst == msg.interceptor_id or self._drop(msg, dst):
                    continue
                ag.picture.on_peer_state(msg)

    def coverage(self) -> dict[str, str]:
        """Ground-truth {track -> the single interceptor on it}; raises on double."""
        out: dict[str, str] = {}
        for ag in self.agents.values():
            tid = ag.state.assigned_track_id
            if tid is None or not ag.state.alive:
                continue
            assert tid not in out, f"{tid} double-covered by {out[tid]} and {ag.id}"
            out[tid] = ag.id
        return out


class _Agent:
    def __init__(
        self,
        iid: str,
        position: Vec3,
        mesh: _Mesh,
        *,
        lock_threshold_s: float = 0.0,  # 0 => locking disabled (t_icpt < 0 never)
        silence_timeout_s: float = 1e9,  # off unless a test exercises liveness
        range_m: float = 1000.0,
        speed_mps: float = 13.0,
        protected_point: Vec3 = PROTECTED,
    ) -> None:
        self.id = iid
        self.state = InterceptorLocalState(iid, position)
        self.picture = AwarenessPicture(
            iid, staleness_timeout_s=1e9, protected_point=protected_point
        )
        self.proto = RetaskingProtocol(
            self.state,
            self.picture,
            emit_state=mesh.send_state,
            range_m=range_m,
            speed_mps=speed_mps,
            protected_point=protected_point,
            lock_threshold_s=lock_threshold_s,
            bucket_size_s=2.0,
            bucket_hysteresis_s=0.2,
            incumbency_margin=1e-3,
            change_repeat=3,
            heartbeat_period_s=0.2,
            silence_timeout_s=silence_timeout_s,
        )
        mesh.register(self)

    def assign(self, track_id: str | None) -> None:
        self.state.assigned_track_id = track_id
        self.picture.update_self(track_id, self.state.alive, 0.0)

    def see(self, tracks: list[Track]) -> None:
        self.picture.on_tracks(tracks)


def _run(mesh: _Mesh, *, steps: int, dt: float = 0.2, start: float = 0.0) -> None:
    now = start
    agents = list(mesh.agents.values())
    for _ in range(steps):
        mesh.deliver()  # flush previous step's broadcasts (1-tick latency)
        for ag in agents:
            ag.proto.tick(now)
        now += dt
    mesh.deliver()  # let the last round land


# --------------------------------------------------------------------------
# 1. Double cover + a gap -> the lower-priority co-owner yields and fills it.
# --------------------------------------------------------------------------
def test_double_cover_resolves_to_bijection() -> None:
    mesh = _Mesh()
    tracks = [_track("t1", (0, 0, 0)), _track("t2", (50, 0, 0)), _track("t3", (100, 0, 0))]
    i1 = _Agent("i1", (0, 0, 0), mesh)  # alone on t1
    i2 = _Agent("i2", (60, 0, 0), mesh)
    i3 = _Agent("i3", (60, 0, 0), mesh)
    for ag in (i1, i2, i3):
        ag.see(tracks)
    i1.assign("t1")
    i2.assign("t2")
    i3.assign("t2")  # i2,i3 double-cover t2; t3 uncovered

    _run(mesh, steps=20)

    # Every track ends singly covered (coverage() raises on any double).
    assert set(mesh.coverage()) == {"t1", "t2", "t3"}
    assert mesh.coverage()["t1"] == "i1"  # unique live coverer never moved


# --------------------------------------------------------------------------
# 2. Tie on a track -> higher id_rank wins, the loser takes the open track.
# --------------------------------------------------------------------------
def test_tie_broken_by_id_then_fills_gap() -> None:
    mesh = _Mesh()
    tracks = [_track("t1", (0, 0, 0)), _track("t3", (10, 0, 0))]
    i2 = _Agent("i2", (0, 0, 0), mesh)
    i3 = _Agent("i3", (0, 0, 0), mesh)  # identical position to i2
    for ag in (i2, i3):
        ag.see(tracks)
        ag.assign("t1")  # both park on t1; t3 is the only gap

    _run(mesh, steps=12)

    cov = mesh.coverage()
    assert cov == {"t1": "i3", "t3": "i2"}  # i3 (higher rank) keeps t1; i2 yields


# --------------------------------------------------------------------------
# 3. A uniquely-covered live track is never abandoned (re-tasking fills, not shifts).
# --------------------------------------------------------------------------
def test_unique_live_coverer_does_not_retask() -> None:
    mesh = _Mesh()
    tracks = [_track("t1", (0, 0, 0)), _track("t2", (50, 0, 0)), _track("t3", (300, 0, 0))]
    i1 = _Agent("i1", (0, 0, 0), mesh)  # alone on live t1
    i2 = _Agent("i2", (50, 0, 0), mesh)  # alone on live t2
    for ag in (i1, i2):
        ag.see(tracks)
    i1.assign("t1")
    i2.assign("t2")

    _run(mesh, steps=12)

    # No spare to send, so t3 stays uncovered; neither abandons its live track.
    assert mesh.coverage() == {"t1": "i1", "t2": "i2"}


# --------------------------------------------------------------------------
# 4. Target dies -> the owner releases the corpse and re-tasks to a live gap.
# --------------------------------------------------------------------------
def test_dead_target_releases_and_retasks() -> None:
    mesh = _Mesh()
    tracks = [_track("t0", (0, 0, 0), alive=False), _track("t3", (10, 0, 0))]
    i2 = _Agent("i2", (0, 0, 0), mesh)
    i2.see(tracks)
    i2.assign("t0")  # owns a corpse

    _run(mesh, steps=6)

    assert i2.state.assigned_track_id == "t3"  # dropped t0, took the live gap


# --------------------------------------------------------------------------
# 5. An expended interceptor releases and advertises alive=False (frees its track).
# --------------------------------------------------------------------------
def test_expended_releases_and_broadcasts_death() -> None:
    mesh = _Mesh()
    tracks = [_track("t1", (0, 0, 0)), _track("t2", (50, 0, 0))]
    i1 = _Agent("i1", (0, 0, 0), mesh)
    i2 = _Agent("i2", (50, 0, 0), mesh)
    for ag in (i1, i2):
        ag.see(tracks)
    i1.assign("t1")
    i2.assign("t2")

    i2.state.alive = False  # shot down
    _run(mesh, steps=4)

    assert i2.state.assigned_track_id is None
    # Its last broadcast carried the death so peers can free t2.
    last_i2 = [s for s in mesh.states if s.interceptor_id == "i2"][-1]
    assert last_i2.alive is False and last_i2.assigned_track_id is None
    assert i1.picture.coverage().get("t2") is None


# --------------------------------------------------------------------------
# 6. Monotone lock: once locked we never yield, even to a higher-priority peer.
# --------------------------------------------------------------------------
def test_locked_owner_never_yields() -> None:
    mesh = _Mesh()
    t1 = _track("t1", (0, 0, 0))
    i2 = _Agent("i2", (0, 0, 0), mesh, lock_threshold_s=5.0)
    i2.see([t1])
    i2.assign("t1")

    i2.proto.tick(0.0)  # intercept_time == 0 < 5 -> locks
    assert i2.proto.locked is True

    # A higher-priority peer appears claiming t1 (huge key). Locked -> ignore it.
    poacher = InterceptorState(
        "i9", (0, 0, 0), (0, 0, 0), "t1", True, 0.2, owns_priority=(1e9, 0.0, 9.0), seq=1
    )
    i2.picture.on_peer_state(poacher)
    i2.proto.tick(0.2)

    assert i2.proto.locked is True
    assert i2.state.assigned_track_id == "t1"  # held through the challenge


# --------------------------------------------------------------------------
# 7. Stale-safe: a silent peer keeps covering until the timeout, then frees.
# --------------------------------------------------------------------------
def test_silent_peer_holds_then_frees() -> None:
    mesh = _Mesh()
    tracks = [_track("t1", (0, 0, 0)), _track("t2", (50, 0, 0))]
    # i1 alone on t1; i2 alone on t2 then goes silent. silence_timeout 0.6 s.
    i1 = _Agent("i1", (0, 0, 0), mesh, silence_timeout_s=0.6)
    i2 = _Agent("i2", (50, 0, 0), mesh, silence_timeout_s=0.6)
    for ag in (i1, i2):
        ag.see(tracks)
    i1.assign("t1")
    i2.assign("t2")

    _run(mesh, steps=3)  # let i1 hear i2 (t2 covered)
    assert "t2" in i1.picture.coverage()

    mesh.muted.add("i2")  # i2 falls silent
    # Within the timeout i1 still treats t2 as covered (no false retask).
    _run(mesh, steps=2, start=0.6)
    assert i1.state.assigned_track_id == "t1"

    # Past the timeout i1 frees t2 in its picture (would re-task if it had a spare).
    _run(mesh, steps=4, start=2.0)
    assert i1.picture.coverage().get("t2") is None


# --------------------------------------------------------------------------
# 8. Packet-loss sweep -> graceful degradation, still converges (NFR-5).
# --------------------------------------------------------------------------
def _lossy(prob: float, seed: int) -> DropFn:
    rng = random.Random(seed)
    return lambda msg, dst: rng.random() < prob


def test_packet_loss_sweep_still_converges() -> None:
    for prob in (0.0, 0.1, 0.3):
        for seed in (1, 7, 42):
            mesh = _Mesh(drop=_lossy(prob, seed))
            tracks = [
                _track("t1", (0, 0, 0)),
                _track("t2", (50, 0, 0)),
                _track("t3", (100, 0, 0)),
            ]
            i1 = _Agent("i1", (0, 0, 0), mesh)
            i2 = _Agent("i2", (60, 0, 0), mesh)
            i3 = _Agent("i3", (60, 0, 0), mesh)
            for ag in (i1, i2, i3):
                ag.see(tracks)
            i1.assign("t1")
            i2.assign("t2")
            i3.assign("t2")

            _run(mesh, steps=160)  # heavy loss just means more reconvergence cycles

            # Bijection is the contract, not a specific id->track mapping: under
            # loss *who* lands where can differ, but every track stays singly
            # covered (coverage() raises on a double) and none is left behind.
            assert set(mesh.coverage()) == {"t1", "t2", "t3"}, (
                f"prob={prob} seed={seed} -> {mesh.coverage()}"
            )
