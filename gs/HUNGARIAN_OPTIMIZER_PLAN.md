# Plan: Hungarian assignment optimizer in `gs/` (milestone G4)

> Implementation plan + optimizer spec for the ground-station assignment layer.
> Grounded in the repo: the `ThreatAssessment` it consumes and the `Assignment`
> it produces (`contracts/contracts/messages.py`), the `InterceptorConfig` it
> reads (`contracts/contracts/config.py`), the cost contract
> (`docs/architecture.md:241`), and the proven G2/G3 pure-logic-+-publisher split.

**Milestone (workstreams.md G4):** _Hungarian optimizer running on `/gs/threats`,
publishing `/gs/assignments` within 2 s._
**Spec FR-5:** optimal Hungarian assignment minimizing intercept time weighted by
threat · feasibility enforced (range envelope) · full assignment issued within
**2 s** of the go signal · unmatched interceptors hold, uncovered threats flagged.

## What G4 is

The **decision layer**: given the current threat picture (G3's
`ThreatAssessment`s) and the interceptor pool, pair each interceptor to at most
one threat so the fleet neutralises the most dangerous targets soonest. One
`Assignment` per committed interceptor, issued as a single burst at launch (the
GS role ends at launch; re-tasking is Team 3's onboard job).

```
/gs/threats ──► [G4 Hungarian optimizer] ──► /gs/assignments ──► interceptors (Team 3)
ThreatAssessment[]      cost + feasibility        Assignment[]
```

## The cost contract that shapes everything

From `architecture.md:241` / `CLAUDE.md`:

```
C[i][j] = intercept_time[i][j] / threat_score[j]      if distance[i][j] < range[i]
C[i][j] = 1e9   (infeasible)                          otherwise
```

`scipy.optimize.linear_sum_assignment` **minimises** total cost over this matrix.
Lower cost ⇒ preferred pairing ⇒ a fast intercept of a high-threat target. Two
properties to respect:

1. **`threat_score` is the denominator** (G3 already guarantees it strictly
   positive — see `THREAT_SCORER_PLAN.md`). G4 must never divide by zero; if a
   threat arrives with a non-positive score, clamp + log (defence in depth).
2. **`1e9` is a feasibility sentinel, not a real cost.** After solving, any chosen
   pair whose cost ≥ the sentinel is *not* an assignment — that interceptor
   **holds** (FR-5.4). Strip these out; never emit them.

## The one genuinely smart modelling choice: intercept time

`intercept_time[i][j]` is the crux, and the naive `distance / speed` is wrong for
a *moving* target. Solve the **lead-pursuit intercept** instead — the smallest
time `t` at which an interceptor leaving launch point `L` at speed `s` can meet a
threat at `p` moving at `v`:

```
Δ = p − L
|Δ + v·t| = s·t                            # interceptor reaches the lead point in time t
⇒ a·t² + b·t + c = 0,  a = |v|² − s²,  b = 2(Δ·v),  c = |Δ|²
```

- Solve the quadratic; take the **smallest positive root** `t`.
- `intercept_point = p + v·t`  → this is the `Assignment.initial_waypoint` (the
  first PN pursuit point — reused for free).
- **No positive root** (discriminant < 0, or target outruns the interceptor:
  `|v| ≥ s` with the threat opening the range) ⇒ **infeasible** ⇒ cost `1e9`.
- Degenerate `a ≈ 0` (`|v| ≈ s`): fall back to the linear root `t = −c / b`.

This mirrors G3's "closing-speed not raw-speed" insight: model the geometry, not
the scalar. It yields both the cost term **and** the waypoint in one shot.

### Feasibility gates (FR-5.2)

A pair is feasible iff **all** hold (else cost = `1e9`):
- a positive intercept time `t` exists (the quadratic above), **and**
- the interceptor can physically reach it: `s·t ≤ range_i` (≡ `distance < range`,
  the architecture condition), **and**
- *(optional, recommended)* it beats the threat to the target:
  `t < threat.eta_seconds` — no point intercepting after impact. Behind a flag.

## Architecture: mirror G2/G3 exactly (pure logic + thin bus bridge)

| G2/G3 (done/planned) | G4 (this plan) |
|---|---|
| `tracker.py` / `threat_scorer.py` — pure | **`optimizer.py`** — pure `assign(threats, interceptors) → AssignmentResult` |
| `track_publisher.py` / `threat_publisher.py` — bus bridge | **`assignment_publisher.py`** — subscribes `GS_THREATS`, snapshots, solves once on the go-signal, publishes `Assignment` on `GS_ASSIGNMENTS` |
| Unit-tested vs analytic cases | Unit-tested vs analytic + greedy-suboptimality cases |

The optimizer is a **pure, stateless function** of a threat list + an interceptor
list — no bus, no clock — so it is deterministic and trivially unit-testable. The
publisher holds the streaming/snapshot concerns.

### Interceptors, pre-launch

Pre-launch every interceptor sits at the pad, so the pool comes from
`ScenarioConfig.interceptors` (`count`, `speed_mps`, `range_m`, `launch_position`)
— ids `i1..iN`, all sharing `launch_position`/`speed`/`range`. (Post-launch
per-interceptor state would come from `/interceptors/{id}/state`, but that is
Team 3's re-tasking world, out of scope for the pre-launch burst.)

### Threat snapshot (the streaming subtlety)

`GS_THREATS` is a *stream* — one `ThreatAssessment` per track per tick. The
optimizer needs a **coherent snapshot of all current threats** at the go moment.
The publisher buffers the latest assessment per `track_id` (expiring stale ones,
like `track_viewer.py` does) and, on the go-signal, solves once over that
snapshot. This matches `mock_assignments.py`'s "one burst at launch" model.

## Phases

**Phase 0 — Nothing new.** `scipy` already in `gs/pyproject.toml`; `numpy` too.

**Phase 1 — Optimizer** (`gs/gs/optimizer.py`)
- The pure component, fully spec'd below. Builds the cost matrix (intercept-time
  quadratic + feasibility), solves with `linear_sum_assignment`, strips infeasible
  picks, returns assignments + diagnostics (held interceptors, uncovered threats).
  Ships with unit tests.

**Phase 2 — Assignment publisher** (`gs/gs/assignment_publisher.py`)
- Subscribes `Topics.GS_THREATS`, buffers latest-per-track, exposes
  `assign_now()` (the go-signal) which snapshots, runs the optimizer, publishes
  one `Assignment` per committed interceptor on `Topics.GS_ASSIGNMENTS`, and logs
  held interceptors / uncovered threats. Mirrors `track_publisher.py`.

**Phase 3 — Wire into a node** (`gs/gs/assignment_node.py`, or extend `gs_node.py`)
- ZeroMQ: subscribe `GS_THREATS`, publish `GS_ASSIGNMENTS`. Fire `assign_now()`
  on a go trigger (one-shot after threats stabilise, or a key/CLI signal). Log the
  resulting pairing table. Replaces the greedy `mock_assignments.py` for real.

**Phase 4 — Tests** (`gs/tests/`)
- `test_optimizer.py` — pure unit (acceptance below).
- `test_assignment_publisher.py` — integration via `MockBroker`: publish
  `ThreatAssessment`s on `GS_THREATS`, fire `assign_now()`, assert `Assignment`s
  on `GS_ASSIGNMENTS` + correct held/uncovered diagnostics.
- *(optional)* end-to-end smoke: `tracker → threat_scorer → optimizer` once G3
  lands on its branch.

---

# Spec: Assignment optimizer (`gs/gs/optimizer.py`)

### Purpose

Pure, deterministic Hungarian assignment of interceptors to threats. Builds the
`intercept_time / threat_score` cost matrix with feasibility gating, solves it
optimally, and returns the committed `Assignment`s plus what was left uncovered.
Bus-free and clock-free so it is unit-testable in isolation.

### Interface

```python
# gs/gs/optimizer.py
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from contracts.messages import Assignment, ThreatAssessment
from scipy.optimize import linear_sum_assignment

Vec3 = tuple[float, float, float]
INFEASIBLE = 1e9


@dataclass(frozen=True)
class Interceptor:
    """Pre-launch interceptor: id + pad position + kinematics (from config)."""
    interceptor_id: str
    launch_position: Vec3
    speed_mps: float
    range_m: float


@dataclass(frozen=True)
class AssignmentResult:
    assignments: list[Assignment]          # one per committed interceptor
    held_interceptors: list[str]           # FR-5.4: no feasible/needed target
    uncovered_threats: list[str]           # FR-5.4: no interceptor assigned


def intercept(interceptor: Interceptor, threat: ThreatAssessment
              ) -> tuple[float, Vec3] | None:
    """Smallest positive intercept time + lead point, or None if uncatchable.
    Solves |Δ + v·t| = s·t for t (the lead-pursuit quadratic)."""
    L = interceptor.launch_position
    p, v, s = threat.position, threat.velocity, interceptor.speed_mps
    dx = (p[0] - L[0], p[1] - L[1], p[2] - L[2])
    a = (v[0]**2 + v[1]**2 + v[2]**2) - s * s
    b = 2.0 * (dx[0]*v[0] + dx[1]*v[1] + dx[2]*v[2])
    c = dx[0]**2 + dx[1]**2 + dx[2]**2
    t = _smallest_positive_root(a, b, c)
    if t is None:
        return None
    point = (p[0] + v[0]*t, p[1] + v[1]*t, p[2] + v[2]*t)
    return t, point


class AssignmentOptimizer:
    """Hungarian interceptor→threat assignment over the architecture cost matrix.

    Tuning / policy knobs:
    - ``require_beat_eta`` — also require intercept_time < threat.eta_seconds
      (don't intercept after impact). Default True.
    """

    def __init__(self, *, require_beat_eta: bool = True) -> None:
        self._beat_eta = require_beat_eta

    def assign(
        self,
        threats: list[ThreatAssessment],
        interceptors: list[Interceptor],
        timestamp: float,
    ) -> AssignmentResult:
        ...
        # 1. cost[i][j] = intercept_time / threat_score  (or INFEASIBLE)
        #    cache intercept points alongside for the waypoint.
        # 2. rows, cols = linear_sum_assignment(cost)   # rectangular OK
        # 3. for each (i, j): if cost < INFEASIBLE -> Assignment(initial_waypoint=
        #    intercept_point); else interceptor i holds.
        # 4. diagnostics: interceptors with no feasible pick -> held;
        #    threats no interceptor took -> uncovered.
```

### Behavioral contract

- **Input:** a threat snapshot + an interceptor pool + an issue `timestamp`.
- **Output:** an `AssignmentResult` — optimal feasible pairing (≤ 1 threat per
  interceptor, ≤ 1 interceptor per threat), plus held interceptors and uncovered
  threats. Never emits an `Assignment` for an infeasible pair.
- **Optimality:** total cost over committed pairs is the Hungarian minimum — must
  beat greedy on the classic greedy-suboptimal case.
- **Feasibility:** every emitted pair has finite cost, `s·t ≤ range`, positive
  `t`, and (if enabled) `t < eta`.
- **Rectangular:** `#interceptors ≠ #threats` handled — surplus on either side
  falls into held / uncovered.
- **Determinism:** pure; identical inputs ⇒ identical assignment (scipy is
  deterministic on a fixed matrix). Break score/cost ties by `interceptor_id`
  then `track_id` for stability.
- **Empty inputs:** no threats ⇒ all interceptors held; no interceptors ⇒ all
  threats uncovered. Must not raise.

### Acceptance tests (`gs/tests/test_optimizer.py`)

1. **2×2 trivial:** two interceptors, two well-separated threats ⇒ each takes the
   nearer/more-threatening; total cost is minimal; both `initial_waypoint`s lead
   the targets correctly.
2. **Threat weighting under scarcity:** 1 interceptor, 2 reachable threats ⇒ it
   takes the **higher `threat_score`** one; the other is uncovered.
3. **Hungarian beats greedy:** the crafted matrix where nearest-first greedy is
   globally suboptimal ⇒ optimizer's total cost is strictly lower.
4. **Range infeasibility (FR-5.2):** a threat beyond every interceptor's range ⇒
   uncovered, never assigned at `1e9`.
5. **Uncatchable target:** threat outrunning interceptor speed and opening range ⇒
   `intercept()` is None ⇒ infeasible.
6. **Lead point correctness:** stationary threat ⇒ `intercept_point == position`,
   `t == distance/speed`; known crossing velocity ⇒ analytic lead point.
7. **Rectangular / empty:** more interceptors than threats ⇒ surplus held; no
   threats ⇒ all held; no interceptors ⇒ all uncovered.
8. **beat-eta gate:** threat that would be reached *after* its `eta_seconds` ⇒
   infeasible when `require_beat_eta=True`.

### Out of scope

Track fusion (G2), threat scoring (G3 — separate branch), onboard re-tasking /
claim-and-confirm (Team 3), per-interceptor live positions, and multi-wave
re-assignment. The pre-launch burst is one optimal solve.

---

## Open questions before building (low-stakes, with defaults)

1. **Intercept model** — lead-pursuit quadratic (recommended) vs straight-line
   `distance/speed` proxy (as `mock_assignments.py` uses). *Default: quadratic,
   with the straight-line value as the `a≈0` fallback.*
2. **Go-signal trigger** — one-shot after the threat snapshot stabilises, vs an
   explicit CLI/key signal. *Default: expose `assign_now()`; the node fires it
   once on a stabilisation heuristic, overridable manually.*
3. **`require_beat_eta`** — gate intercepts that land after impact? *Default: on.*
4. **Seal `GS_ASSIGNMENTS`?** Crosses the Team-2→Team-3 boundary. *Default:
   plaintext MVP (matches tracks/threats today); seal as a follow-up.*
5. **Interceptor identity source** — config-derived `i1..iN` at the pad
   (pre-launch) vs `/interceptors/{id}/state`. *Default: config; this is the
   pre-launch optimizer.*

## Appendix: where G4 sits

```
/radar/detections → [G1/G2] → /gs/tracks → [G3] → /gs/threats → [G4] → /gs/assignments → interceptors
      done            done       done      other branch           THIS PLAN        Team 3
```

G4 is the **load-bearing** milestone: it produces the artefact the whole
Situation-A-vs-B comparison rests on, and it can run **today** on a flat stub
`threat_score = 1.0` (minimising pure intercept time) while G3 lands in parallel —
then G3's real scores drop in behind the same cost matrix with zero structural
change. Build order: **G4 first (stub score), G3 swaps in behind it.**
The 2-second deadline (FR-5.3) is not a concern at fleet scale — Hungarian on a
handful of rows is sub-millisecond; the budget is for end-to-end latency, worth a
one-line timing log, nothing more.
