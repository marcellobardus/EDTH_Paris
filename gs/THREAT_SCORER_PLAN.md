# Plan: Threat scorer in `gs/` (milestone G3)

> Implementation plan + scorer spec for the ground-station threat-assessment
> layer. Grounded in the existing repo: the `Track` it consumes
> (`contracts/contracts/messages.py`), the proven G2 publisher pattern it mirrors
> (`gs/gs/track_publisher.py`), the `ThreatAssessment` message it produces, and
> the G4 cost function it must feed (`docs/architecture.md:241`).

**Milestone (workstreams.md G3):** _Threat scorer running on `/gs/tracks`,
publishing `/gs/threats`._
**Spec:** FR-4.1 score by distance-to-target, time-to-impact, speed · FR-4.2
scores drive assignment priority.

## The design constraint that shapes everything

G3's output is not read by a human — it is read by **G4's cost function**:

```
C[i][j] = intercept_time[i][j] / threat_score[j]      (architecture.md:241)
```

`threat_score` is a **denominator**. Three properties are therefore non-negotiable
and drive the whole spec:

1. **Strictly positive.** Zero ⇒ divide-by-zero; negative ⇒ flips the sign of the
   cost and makes the Hungarian optimizer prefer the *safest* target. Enforce with
   a hard floor + assertion.
2. **Higher = more dangerous** (so lower cost ⇒ preferred interceptor pairing).
3. **Scaled against `intercept_time`** (tens of seconds) so neither term swamps
   the ratio.

Design to the consumer, not to intuition about "what feels threatening."

## Architecture: mirror G2 exactly (pure logic + thin bus bridge)

G2 already proved the right shape. Copy it; do not invent a new one.

| G2 (done) | G3 (this plan) |
|---|---|
| `tracker.py` — pure `process(dets) → Track[]` | **`threat_scorer.py`** — pure `score(track) → ThreatAssessment` |
| `track_publisher.py` — subscribes feeds, publishes `Track` on `GS_TRACKS` | **`threat_publisher.py`** — subscribes `GS_TRACKS`, publishes `ThreatAssessment` on `GS_THREATS` |
| Unit-tested vs analytic trajectories | Unit-tested vs analytic threats |

The scorer is a **pure, stateless function of one track + the target position** —
no bus, no history — so it is deterministic and trivially unit-testable, exactly
like `predictor.py`. The publisher is structurally identical to
`track_publisher.py` (~30 lines): subscribe → score each track → publish.

## The scoring model: `eta` is the spine

FR-4.1 lists distance, time-to-impact, and speed — but **time-to-impact already
contains distance and speed**. Don't triple-count. Compute `eta` physically and
let it carry the signal:

```
to_target      = target_position − track.position
distance       = ‖to_target‖
closing_speed  = track.velocity · (to_target / distance)   # component TOWARD target
eta_seconds    = distance / closing_speed     if closing_speed > 0
               = +inf  (→ floor score)        if receding / not closing
```

The `closing_speed` projection is the one genuinely smart modelling choice: a
drone flying *past* the target at 200 m/s is far less dangerous than a slow one
heading *straight in*. Raw speed would rank them backwards.

Score = imminence, made positive and O(1):

```
threat_score = eta_ref / max(eta_seconds, eta_floor)       # higher when sooner
```

- `eta_floor` (≈ 1 s) caps the score for an about-to-hit track so it can't blow up
  the G4 ratio.
- Receding tracks → `eta = +inf` → score clamped to a small positive floor
  (`min_score`), never exactly 0.
- `eta_ref` (the scenario's engagement horizon, e.g. 60 s) sets the scale relative
  to `intercept_time`.

**Optional speed bonus (NOT MVP):** to honour FR-4.1's explicit "speed" (a faster
missile = less reaction margin), a single mild term
`+ w_speed * (speed / speed_ref)` with a documented weight. Flagged optional —
over-parameterising a number that feeds a ratio is the trap. MVP ships pure
eta-imminence; add the term only if scenarios show a need.

## Phases

**Phase 0 — Nothing new.** No new dependencies (`numpy` already in
`gs/pyproject.toml`). Reuses `contracts` + the `Bus`.

**Phase 1 — Threat scorer** (`gs/gs/threat_scorer.py`)
- The focused, pure component, fully spec'd below. Ships with unit tests.

**Phase 2 — Threat publisher** (`gs/gs/threat_publisher.py`)
- Subscribes `Topics.GS_TRACKS`, scores each `Track`, publishes
  `ThreatAssessment` on `Topics.GS_THREATS`. Mirrors `TrackPublisher`: holds only
  the `Bus` + scorer + `target_position`; an optional `on_threats` callback for
  the node CLI to log. Dead tracks (`alive=False`) are dropped, not scored.

**Phase 3 — Wire into the node** (`gs/gs/gs_node.py`)
- Instantiate `ThreatPublisher(bus, target_position=cfg.target_position)` alongside
  the existing `TrackPublisher`. The track publisher emits `GS_TRACKS`; the threat
  publisher consumes them on the same bus and emits `GS_THREATS`. Log a one-line
  ranked summary per tick (highest score first). No new transport code.

**Phase 4 — Tests** (`gs/tests/`)
- `test_threat_scorer.py` — pure unit (acceptance criteria below).
- `test_threat_publisher.py` — integration via `MockBroker`: publish `Track`s on
  `GS_TRACKS`, assert `ThreatAssessment`s appear on `GS_THREATS` with correct
  ranking and that dead tracks are dropped. Reuse the deterministic pattern from
  `gs/tests/test_track_publisher.py`.
- Optionally an end-to-end smoke test: `StoneSoupRadar → tracker → scorer`,
  assert the track closing fastest gets the top score.

---

# Spec: Threat scorer (`gs/gs/threat_scorer.py`)

### Purpose

A pure, deterministic function that maps one fused `Track` to a
`ThreatAssessment`, given the defended `target_position`. Owns the
distance/closing-speed/eta kinematics and the positive, monotonic score the
Hungarian optimizer consumes. Stateless and bus-free so it is unit-testable in
isolation and reusable per-track.

### State convention (must match the rest of GS)

Positions/velocities are `(x, y, z)` metres / m·s⁻¹, matching
`contracts.messages.Track` and the `[x, vx, y, vy, z, vz]` world used by the
tracker and radar. `target_position` is `cfg.target_position` from
`ScenarioConfig`.

### Interface

```python
# gs/gs/threat_scorer.py
from __future__ import annotations

import math

from contracts.messages import ThreatAssessment, Track

Vec3 = tuple[float, float, float]


class ThreatScorer:
    """Scores a fused track by imminence of impact on the defended target.

    The score is a STRICTLY POSITIVE scalar consumed as the denominator of the
    G4 cost  C[i][j] = intercept_time / threat_score  — higher means more
    dangerous (sooner impact), so the optimizer prefers it.

    Tuning knobs (hackathon-reasonable defaults):

    - ``eta_ref``  — reference horizon (s) setting the score's scale against
      intercept_time. Default 60.0.
    - ``eta_floor`` — minimum eta (s); caps the score for an about-to-hit track
      so it can't dominate the G4 ratio. Default 1.0.
    - ``min_score`` — positive floor returned for receding / non-closing tracks,
      guaranteeing the denominator is never 0. Default 1e-3.
    """

    def __init__(
        self,
        target_position: Vec3,
        *,
        eta_ref: float = 60.0,
        eta_floor: float = 1.0,
        min_score: float = 1e-3,
    ) -> None:
        self._target = target_position
        self._eta_ref = eta_ref
        self._eta_floor = eta_floor
        self._min_score = min_score

    def score(self, track: Track) -> ThreatAssessment:
        """Map one track to a ThreatAssessment. Pure: no I/O, no state mutation."""
        dx = self._target[0] - track.position[0]
        dy = self._target[1] - track.position[1]
        dz = self._target[2] - track.position[2]
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)

        if distance == 0.0:
            eta = self._eta_floor
        else:
            closing = (
                track.velocity[0] * dx
                + track.velocity[1] * dy
                + track.velocity[2] * dz
            ) / distance                        # velocity component toward target
            eta = distance / closing if closing > 0 else math.inf

        if math.isinf(eta):
            threat = self._min_score            # receding / not closing
        else:
            threat = max(
                self._eta_ref / max(eta, self._eta_floor),
                self._min_score,
            )

        return ThreatAssessment(
            track_id=track.track_id,
            position=track.position,
            velocity=track.velocity,
            threat_score=threat,
            eta_seconds=eta,
            timestamp=track.timestamp,
        )
```

### Behavioral contract

- **Input:** one `Track` (assumed `alive`; the publisher filters dead tracks) and
  the fixed `target_position`.
- **Output:** a `ThreatAssessment` with `eta_seconds` (physical, may be `+inf` for
  receding tracks) and `threat_score` (**strictly positive, finite**, higher ⇒
  more dangerous).
- **Monotonicity (the property G4 relies on):** holding all else equal, a smaller
  `eta` yields a strictly larger `threat_score` until clamped at `eta_floor`.
- **Receding / zero closing speed:** `eta = +inf`, `threat_score = min_score`
  (positive, so the G4 ratio is always well-defined).
- **Determinism:** pure arithmetic, no RNG, no state — identical inputs give
  identical outputs.
- **Stateless across calls:** owns only the target + constants; reusable for every
  track and every tick.

### Tuning knobs

- `eta_ref` — score scale vs `intercept_time`; raise to make threat dominate the
  G4 ratio, lower to make intercept time dominate.
- `eta_floor` — saturation point for imminent threats.
- `min_score` — strictly-positive floor (divide-by-zero guard for G4).
- Recommend exposing all three via `ScenarioConfig` (a `threat:` block) later,
  exactly as the tracker plan defers its knobs — constructor args for the MVP.

### Acceptance tests (`gs/tests/test_threat_scorer.py`)

1. **Head-on eta is exact:** track at `(-3000,0,100)` velocity `(50,0,0)`, target
   at origin-plane ⇒ `eta ≈ 3000/50 = 60 s`; `threat_score == eta_ref/60`.
2. **Closer-and-faster outscores farther-and-slower** (monotonicity G4 needs).
3. **Receding track:** velocity pointing away ⇒ `eta == inf`,
   `threat_score == min_score`, and **> 0**.
4. **Imminent track is clamped:** `eta < eta_floor` ⇒ score uses `eta_floor` (no
   blow-up), still finite.
5. **Strict positivity invariant:** over a sweep of random tracks,
   `0 < threat_score < inf` always.
6. **Tangential pass:** velocity perpendicular to the line-of-sight to target ⇒
   `closing ≈ 0` ⇒ near-floor score (a fly-past is not a threat).

### Explicitly out of scope for this component

The Hungarian assignment (G4), feasibility/range gating (G4 / FR-5.2), multi-track
ranking beyond per-track scores (the publisher/node sorts), track lifecycle (G2),
and any learned/Bayesian scoring. The optional speed-bonus term is out of MVP.

---

## Open questions before building (low-stakes, with defaults)

1. **Explicit speed term?** Pure eta-imminence (MVP) vs. add
   `w_speed * speed/speed_ref`. *Default: pure eta; add later only if scenarios
   show fast fly-bys are mis-ranked.*
2. **`eta_seconds` for receding tracks** — emit `inf` (honest, but JSON-serialises
   to `Infinity`) or a large sentinel (e.g. `1e9`)? *Default: large sentinel for
   wire-safety, since the bus JSON-encodes dataclasses; document it.*
3. **Should `GS_THREATS` be `SecureContract.seal`'d?** Crosses the Team-2→Team-3
   boundary like `GS_TRACKS`. *Default: plaintext for the MVP (matches how tracks
   publish today); seal as a follow-up once G3→G4 works end-to-end.*
4. **Dead-track policy** — drop, or emit a zero-ish assessment? *Default: the
   publisher drops `alive=False` tracks; the scorer itself never sees them.*

## Appendix: where this sits in the GS pipeline

```
/radar/detections → [G1/G2 tracker] → /gs/tracks → [G3 scorer] → /gs/threats → [G4 Hungarian] → /gs/assignments
        done                              done         THIS PLAN                  next (G4)
```

G3 is deliberately small — roughly one tracker-phase of work — because the hard
modelling (motion, association, lifecycle) already lives in G1/G2. The value G3
adds is a single, well-behaved, G4-ready number per track.
