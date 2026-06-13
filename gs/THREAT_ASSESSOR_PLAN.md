# Plan: Threat assessor in `gs/`

> Implementation plan + component spec for the ground-station threat-scoring
> layer — the stage between track fusion (done) and assignment (planned).
> Grounded in the repo: the `Track` stream the tracker already publishes, the
> `ThreatAssessment` contract, and `ScenarioConfig.target_position` (the defended
> asset).

## Where it sits

```
/gs/tracks                 THREAT ASSESSOR                 /gs/threats            /gs/assignments
Track ─────────────────►  score each track against  ──────────────────►  ( Hungarian ─────────► )
(fused, clean)            the defended asset             ThreatAssessment      assignment (planned)
                          (this plan)
```

The assessor answers, per track: **"how dangerous is this drone, and when does it
arrive?"** Its output feeds the assignment optimizer, whose cost matrix is
`C[i][j] = intercept_time(i,j) / threat_score(j)` — so `threat_score` must be
**positive** and **monotone with urgency**, and the scale only needs to be
consistent, not physical.

## Inputs / outputs

| | Topic | Message | Notes |
|---|---|---|---|
| **In** | `/gs/tracks` | `Track` | fused track: `position`, `velocity`, `covariance` |
| **Config** | — | `ScenarioConfig.target_position` | the defended asset (e.g. `[500, 500, 0]`) |
| **Out** | `/gs/threats` | `ThreatAssessment` | `{track_id, position, velocity, threat_score, eta_seconds, timestamp}` |

Scoring is **per-track and independent** — unlike tracking (which needs joint
association), there is no cross-track coupling. So the assessor is a pure,
stateless map `Track → ThreatAssessment`; no batching, no internal state.

## Threat model (the one real design decision)

Asset-defence model: a drone is threatening to the extent it is **closing on the
defended point**. Using straight-line constant-velocity extrapolation of the
track:

```
to_target    = target_position - track.position
distance     = ‖to_target‖
closing_speed = dot(track.velocity, to_target) / distance     # radial speed toward asset
```

- **`eta_seconds`** — time to reach the asset:
  - `distance / closing_speed` when `closing_speed > MIN_CLOSING` (genuinely inbound),
  - `ETA_SENTINEL` (`1e9`) when receding or merely crossing (not closing),
  - `0.0` when already at the asset (`distance ≈ 0`).
- **`threat_score`** — higher = more dangerous, defined as urgency:
  - `1.0 / max(eta_seconds, EPS_ETA)` — imminent threats score highest; a
    non-closing track gets `~1e-9` (tiny but **strictly positive**, so the
    assignment cost `intercept_time / threat_score` stays finite — no divide-by-zero).

This makes `threat_score ≈ 1/eta`, which is clean and divide-safe. `ETA_SENTINEL`
is a large finite number (not `math.inf`) so it round-trips through the bus's
JSON serialisation cleanly and mirrors the assignment's `1e9` infeasibility
convention.

**Deliberately out of scope for v1** (noted as upgrade paths): altitude
weighting, time-to-*closest-approach* (vs time-to-impact), and
covariance/confidence weighting (down-rank uncertain tracks). The interface
doesn't change if we add these later — only the score formula does.

## Phases

**Phase 1 — `ThreatAssessor`** (`gs/gs/threat_assessor.py`)
- Pure logic: `assess(track: Track) -> ThreatAssessment`. The spec below. Ships
  with its own unit tests. No bus, no config loading — takes `target_position`
  in its constructor.

**Phase 2 — Wire into the node** (`gs/gs/gs_node.py`)
- Load `ScenarioConfig` (add `--config`, default `config/scenario_default.yaml`)
  to get `target_position`.
- Construct a `ThreatAssessor(target_position)`. In the existing `on_tracks`
  hook (which already receives each tick's tracks), score every track and
  publish its `ThreatAssessment` on `/gs/threats`.
- **No new ZeroMQ endpoint:** `/gs/threats` publishes on the *same* outbound PUB
  socket as `/gs/tracks` (a PUB socket carries many topics; subscribers filter).
  So `tracks_out` already covers it.
- Rationale for in-process: tracking and threat scoring are both GS
  responsibilities (per CLAUDE.md), scoring is a cheap stateless per-track map,
  and doing it inline avoids re-subscribing to our own track output. *(Alternative
  if process isolation is wanted later: a standalone node subscribing `/gs/tracks`
  and publishing `/gs/threats` — the `ThreatAssessor` class is unchanged either
  way.)*

**Phase 3 — Tests** (`gs/tests/test_threat_assessor.py`)
- Unit (Phase 1 criteria below) + a small integration check that the assessor
  consumes the tracker's real `Track` output.

---

# Spec: Threat assessor (`gs/gs/threat_assessor.py`)

### Purpose
Score a single fused `Track` against the defended asset, producing a
`ThreatAssessment` with a positive, urgency-monotone `threat_score` and an
`eta_seconds` time-to-impact. Pure and stateless so the score formula is the only
thing that ever changes and it is trivially unit-testable.

### Interface

```python
# gs/gs/threat_assessor.py
from __future__ import annotations

import math

from contracts.messages import ThreatAssessment, Track

Vec3 = tuple[float, float, float]

ETA_SENTINEL = 1e9    # seconds; "not closing / never arrives" (finite, JSON-safe)
EPS_ETA = 1e-3        # floor so threat_score stays finite for at-asset tracks


class ThreatAssessor:
    """Scores one track against a fixed defended asset. Stateless.

    `min_closing_speed` (m/s) is the threshold below which a track counts as
    not inbound (receding or crossing) — its eta becomes ETA_SENTINEL and its
    threat_score collapses toward zero.
    """

    def __init__(self, target_position: Vec3, *, min_closing_speed: float = 1.0) -> None:
        self._target = target_position
        self._min_closing = min_closing_speed

    def assess(self, track: Track) -> ThreatAssessment:
        eta = self._eta_seconds(track.position, track.velocity)
        return ThreatAssessment(
            track_id=track.track_id,
            position=track.position,
            velocity=track.velocity,
            threat_score=1.0 / max(eta, EPS_ETA),
            eta_seconds=eta,
            timestamp=track.timestamp,
        )

    def _eta_seconds(self, position: Vec3, velocity: Vec3) -> float:
        tx, ty, tz = self._target
        dx, dy, dz = tx - position[0], ty - position[1], tz - position[2]
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance < 1e-6:
            return 0.0  # already at the asset — maximally urgent
        closing = (velocity[0] * dx + velocity[1] * dy + velocity[2] * dz) / distance
        if closing <= self._min_closing:
            return ETA_SENTINEL  # receding or crossing — not a closing threat
        return distance / closing
```

### Behavioral contract
- **Input:** one `Track` (position/velocity in metres, m/s); a fixed
  `target_position`.
- **Output:** a `ThreatAssessment` carrying the track's id/position/velocity/
  timestamp verbatim, plus a strictly-positive `threat_score` and an
  `eta_seconds ≥ 0`.
- **Closing track** → `eta = distance / closing_speed`, `score = 1/eta`.
- **Non-closing** (receding/crossing) → `eta = ETA_SENTINEL`, `score ≈ 1e-9`
  (positive, so downstream cost is finite).
- **At asset** (`distance ≈ 0`) → `eta = 0`, `score = 1/EPS_ETA` (capped max).
- **Pure / stateless** — same input → same output; no RNG, no time, no I/O.

### Acceptance tests (`gs/tests/test_threat_assessor.py`)
1. **Head-on inbound:** track 1000 m out moving straight at the asset at 50 m/s →
   `eta ≈ 20 s`, `threat_score ≈ 0.05`.
2. **Closer/faster scores higher:** halving distance or doubling closing speed
   raises `threat_score` (monotone with 1/eta).
3. **Receding track:** velocity pointing away → `eta == ETA_SENTINEL`,
   `threat_score` tiny but `> 0` (no divide-by-zero downstream).
4. **Crossing track:** velocity perpendicular to the line-of-sight → treated as
   non-closing (eta sentinel).
5. **At asset:** distance ≈ 0 → `eta == 0`, finite capped `threat_score`.
6. **Passthrough:** `track_id`, `position`, `velocity`, `timestamp` copied
   unchanged onto the `ThreatAssessment`.
7. **Cost-formula safety:** for any track, `intercept_time / threat_score` is
   finite (i.e. `threat_score > 0` always).

### Out of scope for this component
Bus/transport, config loading, the assignment optimizer, and the richer threat
factors (altitude, time-to-closest-approach, covariance weighting).

---

## Open questions (low-stakes, with defaults)
1. **Threat metric** — time-to-impact at the asset (default) vs time-to-closest-
   approach (captures near-misses that never "arrive"). *Default: time-to-impact;
   it matches the asset-defence framing and the assignment cost.*
2. **Emit policy** — publish a `ThreatAssessment` for every live track (default,
   with floored score) vs filter out non-closing tracks. *Default: emit all —
   viz/telemetry want the full picture, and the floored score naturally
   deprioritises non-threats in assignment.*
3. **Score scale** — raw `1/eta` (default) vs a normalised/bounded score. *Default:
   raw `1/eta`; the Hungarian cost only needs monotonicity, not a fixed range.*
4. **Wiring** — in-process in `gs_node` (default) vs a standalone node. *Default:
   in-process; the class is identical either way.*

## See also
- `KALMAN_TRACKER_PLAN.md` — the upstream tracker this consumes.
- `README.md` — component overview (threat scoring listed as "planned").
- `contracts/contracts/messages.py` — `ThreatAssessment`; `config.py` —
  `ScenarioConfig.target_position`.
