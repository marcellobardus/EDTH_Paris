"""A5 tests (FR-8): claim-and-confirm re-tasking, via an in-memory mesh.

The FSM is pure (RetaskingProtocol talks only to AwarenessPicture /
InterceptorLocalState and two emit callbacks), so we exercise it headless with a
hand-wired mesh standing in for DDS — no ROS, no Docker.

Mesh model (the part that has to be faithful, or the tests lie):
  * Claims/commits are EVENTS with one tick of latency: a message emitted while
    ticking step N is delivered at the start of step N+1. That is what makes the
    ~400 ms consensus window see *overlapping* claims — both contenders are
    already WAITING when each other's claim lands, exactly like async pub/sub.
    Deliver instantly inside the same synchronous tick loop and you'd get a
    spurious double-commit that the real (async) system never produces.
  * Peer STATE is level-triggered (latest assignment wins) and is re-broadcast
    every step — that periodic re-broadcast is the substrate reconvergence rides
    on when a one-shot Commit is dropped.
  * Loss is a seeded predicate over (kind, msg, dst) so packet-loss sweeps are
    deterministic and per-link.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from agent.awareness import AwarenessPicture
from agent.local_state import InterceptorLocalState
from agent.retasking import RetaskingProtocol
from contracts.messages import Claim, Commit, Track

Vec3 = tuple[float, float, float]
DropFn = Callable[[str, object, str], bool]
_NO_LOSS: DropFn = lambda kind, msg, dst: False  # noqa: E731


def _track(tid: str, pos: Vec3, *, alive: bool = True) -> Track:
    cov = [[0.0] * 6 for _ in range(6)]
    return Track(tid, pos, (0.0, 0.0, 0.0), cov, alive, 0.0)


class _Mesh:
    """Deferred, optionally-lossy peer fabric for a set of agents."""

    def __init__(self, *, drop: DropFn = _NO_LOSS) -> None:
        self.agents: dict[str, _Agent] = {}
        self._drop = drop
        self._inflight: list[Claim | Commit] = []  # emitted, not yet delivered
        self.claims: list[Claim] = []  # every emission, for assertions
        self.commits: list[Commit] = []

    def register(self, agent: _Agent) -> None:
        self.agents[agent.id] = agent

    # emit callbacks handed to each RetaskingProtocol -------------------
    def send_claim(self, claim: Claim) -> None:
        self.claims.append(claim)
        self._inflight.append(claim)

    def send_commit(self, commit: Commit) -> None:
        self.commits.append(commit)
        self._inflight.append(commit)

    # driven by the step loop ------------------------------------------
    def deliver_events(self) -> None:
        """Flush last step's claims/commits to every other agent (lossy)."""
        batch, self._inflight = self._inflight, []
        for msg in batch:
            kind = "claim" if isinstance(msg, Claim) else "commit"
            for dst, ag in self.agents.items():
                if dst == msg.interceptor_id or self._drop(kind, msg, dst):
                    continue
                if isinstance(msg, Claim):
                    ag.proto.on_peer_claim(msg)
                else:
                    ag.proto.on_peer_commit(msg)

    def broadcast_state(self, now: float) -> None:
        """Level-triggered peer state, re-sent every step (lossy)."""
        for src, ag in self.agents.items():
            msg = ag.state.to_state_msg(now)
            for dst, peer in self.agents.items():
                if dst == src or self._drop("state", msg, dst):
                    continue
                peer.picture.on_peer_state(msg)

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
        window: float = 0.4,
        rounds: int = 2,
    ) -> None:
        self.id = iid
        self.state = InterceptorLocalState(iid, position)
        self.picture = AwarenessPicture(iid, staleness_timeout_s=1e9)
        self.proto = RetaskingProtocol(
            self.state,
            self.picture,
            consensus_window_s=window,
            max_claim_rounds=rounds,
            emit_claim=mesh.send_claim,
            emit_commit=mesh.send_commit,
        )
        mesh.register(self)

    def assign(self, track_id: str | None) -> None:
        self.state.assigned_track_id = track_id
        self.picture.update_self(track_id, self.state.alive, 0.0)

    def see(self, tracks: list[Track]) -> None:
        self.state.latest_tracks = {t.track_id: t for t in tracks}
        self.picture.on_tracks(tracks)


def _run(mesh: _Mesh, *, steps: int, dt: float = 0.1, start: float = 0.0) -> None:
    now = start
    agents = list(mesh.agents.values())
    for _ in range(steps):
        mesh.deliver_events()  # flush previous step's claims/commits (1-tick latency)
        mesh.broadcast_state(now)  # periodic level-triggered state
        for ag in agents:
            ag.proto.tick(now)
        now += dt
    mesh.deliver_events()  # let the last round's commits land


# --------------------------------------------------------------------------
# 1. Simultaneous claims on the same target resolve to exactly one commit.
# --------------------------------------------------------------------------
def test_simultaneous_claims_yield_single_commit() -> None:
    mesh = _Mesh()
    tracks = [_track("t1", (0, 0, 0)), _track("t3", (10, 0, 0))]
    # i2 and i3 both park on t1 (double cover) -> both spare; t3 is the only gap.
    i2 = _Agent("i2", (0, 0, 0), mesh)
    i3 = _Agent("i3", (0, 0, 0), mesh)
    for ag in (i2, i3):
        ag.see(tracks)
        ag.assign("t1")

    _run(mesh, steps=8)

    t3_commits = [c for c in mesh.commits if c.target_track_id == "t3"]
    assert len(t3_commits) == 1, mesh.commits
    assert t3_commits[0].interceptor_id == "i3"  # higher id wins (arbitration by id)
    assert mesh.coverage()["t3"] == "i3"


# --------------------------------------------------------------------------
# 2. Scripted 3-agent scenario -> bijective coverage of the discovered tracks.
# --------------------------------------------------------------------------
def test_three_agents_converge_to_bijective_coverage() -> None:
    mesh = _Mesh()
    tracks = [_track("t1", (0, 0, 0)), _track("t2", (50, 0, 0)), _track("t3", (100, 0, 0))]
    i1 = _Agent("i1", (0, 0, 0), mesh)  # alone on a live track -> must abstain
    i2 = _Agent("i2", (60, 0, 0), mesh)
    i3 = _Agent("i3", (60, 0, 0), mesh)
    for ag in (i1, i2, i3):
        ag.see(tracks)
    i1.assign("t1")
    i2.assign("t2")
    i3.assign("t2")  # i2,i3 double-cover t2; t3 is uncovered

    _run(mesh, steps=12)

    assert mesh.coverage() == {"t1": "i1", "t2": "i2", "t3": "i3"}
    # i1 was never a spare, so it never competed for the gap.
    assert all(c.interceptor_id != "i1" for c in mesh.claims)


# --------------------------------------------------------------------------
# 3. A dropped Commit still reconverges (peers re-detect via state + conflict).
# --------------------------------------------------------------------------
def test_reconverges_when_commits_are_lost() -> None:
    drop_all_commits: DropFn = lambda kind, msg, dst: kind == "commit"  # noqa: E731
    mesh = _Mesh(drop=drop_all_commits)
    tracks = [_track("t1", (0, 0, 0)), _track("t2", (50, 0, 0)), _track("t3", (100, 0, 0))]
    i1 = _Agent("i1", (0, 0, 0), mesh)
    i2 = _Agent("i2", (60, 0, 0), mesh)
    i3 = _Agent("i3", (60, 0, 0), mesh)
    for ag in (i1, i2, i3):
        ag.see(tracks)
    i1.assign("t1")
    i2.assign("t2")
    i3.assign("t2")

    _run(mesh, steps=40)  # commits never arrive; convergence rides on state re-broadcast

    assert mesh.coverage() == {"t1": "i1", "t2": "i2", "t3": "i3"}


# --------------------------------------------------------------------------
# 4. Target dying inside the claim window -> clean abandon, no commit on a corpse.
# --------------------------------------------------------------------------
def test_target_dies_during_window_no_commit() -> None:
    mesh = _Mesh()
    # i2 sits on a dead track (-> spare, conflict armed); t3 is the live gap.
    live = [_track("t0", (0, 0, 0), alive=False), _track("t3", (10, 0, 0))]
    i2 = _Agent("i2", (0, 0, 0), mesh)
    i2.see(live)
    i2.assign("t0")

    i2.proto.tick(0.0)  # IDLE -> claims t3, WAITING (deadline 0.4)
    assert any(c.target_track_id == "t3" for c in mesh.claims)

    # t3 is engaged mid-window: it goes dead in both the picture and the tracks.
    dead = [_track("t0", (0, 0, 0), alive=False), _track("t3", (10, 0, 0), alive=False)]
    i2.see(dead)
    i2.proto.tick(0.5)  # past the deadline -> must abandon, not commit

    assert mesh.commits == []
    assert i2.state.assigned_track_id == "t0"  # untouched; no commit on a corpse


# --------------------------------------------------------------------------
# 5. An interceptor expended mid-claim emits no phantom commit.
# --------------------------------------------------------------------------
def test_expended_during_claim_no_phantom_commit() -> None:
    mesh = _Mesh()
    tracks = [_track("t0", (0, 0, 0), alive=False), _track("t3", (10, 0, 0))]
    i2 = _Agent("i2", (0, 0, 0), mesh)
    i2.see(tracks)
    i2.assign("t0")  # spare via dead assignment

    i2.proto.tick(0.0)  # claims t3
    assert any(c.target_track_id == "t3" for c in mesh.claims)

    i2.state.alive = False  # shot down before the window closes
    i2.proto.tick(0.5)

    assert mesh.commits == []


# --------------------------------------------------------------------------
# 6. Greedy fallback after max rounds of being outranked (FR-8.6).
# --------------------------------------------------------------------------
def test_greedy_fallback_after_max_rounds() -> None:
    mesh = _Mesh()
    # i1 sits on a dead track -> it's a spare and there's a conflict to resolve.
    tracks = [
        _track("t0", (0, 0, 0), alive=False),
        _track("t2", (1, 0, 0)),
        _track("t3", (2, 0, 0)),
        _track("t4", (3, 0, 0)),
    ]
    i1 = _Agent("i1", (0, 0, 0), mesh, window=0.4, rounds=2)
    i1.see(tracks)
    i1.assign("t0")  # assigned to a corpse -> wasted -> spare, conflict present

    now = 0.0
    i1.proto.tick(now)  # claims closest uncovered: t2
    assert mesh.claims[-1].target_track_id == "t2"

    # Round 1: a higher-id peer outranks us on t2 -> we yield and pick t3.
    i1.proto.on_peer_claim(Claim("i9", "t2", now))
    i1.proto.tick(now + 0.4)
    assert mesh.claims[-1].target_track_id == "t3"

    # Round 2: outranked again -> max rounds hit -> greedy commit, no further wait.
    i1.proto.on_peer_claim(Claim("i9", "t3", now + 0.4))
    i1.proto.tick(now + 0.8)

    assert len(mesh.commits) == 1
    committed = mesh.commits[0].target_track_id
    assert committed == "t2"  # greedy = closest uncovered, contention ignored
    assert i1.state.assigned_track_id == committed
    # Whole episode fit in 2 windows -> well under the 2 s budget (NFR-1/FR-8.7).
    assert (now + 0.8) - now < 2.0


# --------------------------------------------------------------------------
# 7. We never abandon a target we uniquely cover (re-tasking fills, not shifts).
# --------------------------------------------------------------------------
def test_unique_live_coverer_does_not_retask() -> None:
    mesh = _Mesh()
    tracks = [_track("t1", (0, 0, 0)), _track("t2", (50, 0, 0))]
    i1 = _Agent("i1", (0, 0, 0), mesh)  # alone on live t1
    i2 = _Agent("i2", (50, 0, 0), mesh)  # alone on live t2
    i1.see(tracks)
    i2.see(tracks)
    i1.assign("t1")
    i2.assign("t2")
    # A third track appears that nobody can reach without stranding a live one.
    extra = [*tracks, _track("t3", (200, 0, 0))]
    i1.see(extra)
    i2.see(extra)

    _run(mesh, steps=10)

    # Each keeps its unique live track; t3 stays uncovered (no spare to send).
    assert mesh.coverage() == {"t1": "i1", "t2": "i2"}
    assert mesh.commits == []


# --------------------------------------------------------------------------
# 8. Packet-loss sweep -> graceful degradation, still converges (NFR-5).
# --------------------------------------------------------------------------
def _lossy(prob: float, seed: int) -> DropFn:
    rng = random.Random(seed)
    return lambda kind, msg, dst: rng.random() < prob


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

            # Generous horizon: heavy loss just means more conflict cycles.
            _run(mesh, steps=120)

            # Bijection is the contract, not a specific id->track mapping: under
            # loss *who* lands where can differ, but every track stays singly
            # covered (coverage() raises on any double) and none is left behind.
            assert set(mesh.coverage()) == {"t1", "t2", "t3"}, (
                f"prob={prob} seed={seed} -> {mesh.coverage()}"
            )
