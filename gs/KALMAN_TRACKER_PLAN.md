# Plan: Stone Soup multi-target tracker in `gs/`

> Implementation plan + Kalman predictor spec for the ground-station track-fusion
> layer. Grounded in the existing repo: the `Bus` protocol
> (`contracts/contracts/bus.py`), the Stone Soup radar the tracker must mirror
> (`sim/sim/radar_stonesoup.py`), and the `Track` message contract
> (`contracts/contracts/messages.py`).

## Design constraint that shapes everything

The repo's `Bus` is **callback-driven** (`subscribe(topic, type, handler)`),
whereas Stone Soup's `MultiTargetTracker` expects a *pull* `detector` that yields
`(timestamp, {detections})` on iteration. These don't compose directly.

| Option | Approach | Verdict |
|---|---|---|
| **A — Manual loop** | Buffer detections per scan-tick, drive Stone Soup's components (predictor/updater/hypothesiser/associator/initiator/deleter) ourselves from the callback. | **Recommended.** Fits the push-based bus, gives full control over timing, and the components are the same ones `MultiTargetTracker` uses internally. |
| B — Adapter feed | Wrap the bus in a `DetectionReader` generator and run the stock `MultiTargetTracker`. | More "blessed" but fights the bus model; harder to test deterministically and to control scan cadence. |

We plan around **Option A** — a thin orchestrator built from Stone Soup
primitives, with the **Kalman predictor as the first, independently-testable
unit**.

## Motion model recommendation

Mirror the radar's own model: **`CombinedLinearGaussianTransitionModel([ConstantVelocity(q)]*3)`**
over a 6-D state `[x, vx, y, vy, z, vz]`, with
**`LinearGaussian(ndim_state=6, mapping=(0,2,4))`** measurements. Shaheds are
essentially straight-line cruise missiles, so constant-velocity is a genuinely
good baseline (not a shortcut). Leave a documented upgrade path to ConstantTurn /
IMM-analogue for terminal maneuvers, but do **not** build it in the MVP. Using
the same model family as the radar means the filter is well-matched to the data
by construction.

## Phases

**Phase 0 — Dependency**
- Add `stonesoup>=1.2` to `gs/pyproject.toml` (already present in `sim/`).
  `numpy`/`scipy` already there. `uv sync`.

**Phase 1 — Kalman predictor** (`gs/gs/predictor.py`)
- The focused component, fully spec'd below. Thin wrapper over
  `stonesoup.predictor.kalman.KalmanPredictor`. Ships with its own unit tests.

**Phase 2 — Single-target filter** (`gs/gs/filter.py`)
- Pair the predictor with `KalmanUpdater`. Helper to seed a new `GaussianState`
  from one detection (zero velocity, inflated velocity covariance) and to run
  predict→update for a matched detection. Unit-tested against a synthetic CV
  trajectory: RMSE of filtered position must beat raw detection noise.

**Phase 3 — Multi-target tracker** (`gs/gs/tracker.py`)
- The Option-A orchestrator. Per scan-tick: predict all live tracks to `now` →
  gate + associate detections to predictions → update matched → initiate tracks
  from unmatched (M-of-N) → delete stale tracks. Stone Soup components:
  - Gating/scoring: `DistanceHypothesiser(predictor, updater, measure=Mahalanobis(), missed_distance=3)`
  - Association: `GNNWith2DAssignment` (baseline). Pluggable to `JPDA` for heavy clutter.
  - Initiation: `MultiMeasurementInitiator(min_points=2-3)` (M-of-N style).
  - Deletion: `CovarianceBasedDeleter(covar_trace_thresh=...)`.
- Maintains `track_id -> Stone Soup Track` and emits our `contracts.Track` per
  tick on `Topics.GS_TRACKS`.

**Phase 4 — Track <-> contract mapping** (in `tracker.py`)
- Convert Stone Soup `GaussianState` -> `contracts.messages.Track`:
  - `position = state[0,2,4]`, `velocity = state[1,3,5]`
  - `covariance` = the 6x6, **documented as Stone Soup `[x,vx,y,vy,z,vz]` ordering** (decision point — see Open questions).
  - `track_id` = stable string from Stone Soup track id, `alive=True`,
    `timestamp` from detection clock.

**Phase 5 — Wire into the node** (`gs/gs/gs_node.py`)
- Subscribe `RADAR_DETECTIONS`; buffer detections and tick the tracker on a fixed
  cadence (timer or detection-driven debounce); publish `Track[]` on `GS_TRACKS`.
  Keep `LaunchDecider` but flip it to consume `GS_TRACKS` instead of raw
  detections (separate small change; threat scoring + Hungarian assignment are
  out of scope for this plan).

**Phase 6 — Tests** (`gs/tests/`)
- `test_predictor.py` — unit (Phase 1 acceptance criteria below).
- `test_filter.py` — single-target convergence vs noise.
- `test_tracker.py` — integration via `MockBroker` + `StoneSoupRadar` (3 crossing
  Shaheds, `prob_detect<1`, clutter): assert track count stabilizes to 3, no ID
  explosion, IDs persist across the crossing. Reuse the deterministic-seed
  pattern from `gs/tests/test_radar_to_gs.py`.

## Timing detail (the one real subtlety)

Stone Soup predicts to an absolute `datetime`. Detections carry
`timestamp: float` (seconds since scenario start), and the radar derives it from
a `start_time` datetime. The tracker must hold a `start_time` (datetime) and
convert `float -> datetime` for predictions, consistent with the radar. Make
`start_time` a constructor arg, exactly as `StoneSoupRadar` does.

---

# Spec: Kalman predictor (`gs/gs/predictor.py`)

### Purpose

A thin, deterministic wrapper around Stone Soup's linear Kalman predictor that
owns the **constant-velocity transition model** and projects a track's Gaussian
state forward to a target time. It is the "predict" half of the filter — no
measurements, no association. Isolated so it can be unit-tested and the motion
model swapped in one place.

### State convention (must match the radar)

6-D state vector ordered `[x, vx, y, vy, z, vz]`, metres and m/s. Positions at
indices `(0, 2, 4)`, velocities at `(1, 3, 5)`. This is the Stone Soup
`CombinedLinearGaussianTransitionModel([ConstantVelocity]*3)` layout used in
`sim/sim/radar_stonesoup.py:68-71` — reusing it guarantees the filter and the
simulator agree.

### Stone Soup classes wrapped

- `stonesoup.models.transition.linear.CombinedLinearGaussianTransitionModel`
- `stonesoup.models.transition.linear.ConstantVelocity`
- `stonesoup.predictor.kalman.KalmanPredictor`
- Types: `stonesoup.types.state.GaussianState`, `stonesoup.types.prediction.GaussianStatePrediction`

### Interface

```python
# gs/gs/predictor.py
from __future__ import annotations
from datetime import datetime

import numpy as np
from stonesoup.models.transition.linear import (
    CombinedLinearGaussianTransitionModel, ConstantVelocity,
)
from stonesoup.predictor.kalman import KalmanPredictor
from stonesoup.types.state import GaussianState
from stonesoup.types.prediction import GaussianStatePrediction


class ConstantVelocityPredictor:
    """Projects a 6-D [x,vx,y,vy,z,vz] Gaussian track state forward in time
    under a constant-velocity model. Predict-only; pairs with KalmanUpdater.

    `process_noise` is the CV power-spectral-density q (m^2/s^3) PER AXIS —
    the single most important tuning knob. Larger q => the filter trusts the
    motion model less and adapts faster to maneuvers, at the cost of noisier
    estimates. Start at the radar's process_noise (1.0) and tune up if Shaheds
    maneuver and tracks lag.
    """

    def __init__(self, process_noise: float = 1.0) -> None:
        self._transition = CombinedLinearGaussianTransitionModel(
            [ConstantVelocity(process_noise)] * 3
        )
        self._predictor = KalmanPredictor(self._transition)

    @property
    def transition_model(self) -> CombinedLinearGaussianTransitionModel:
        """Exposed so the paired KalmanUpdater/initiator share one model."""
        return self._transition

    def predict(
        self, prior: GaussianState, timestamp: datetime
    ) -> GaussianStatePrediction:
        """Project `prior` to `timestamp`. dt is taken from the prior's own
        timestamp; predicting to an earlier time raises (no smoothing here)."""
        if prior.timestamp is not None and timestamp < prior.timestamp:
            raise ValueError("predict() target time precedes prior state time")
        return self._predictor.predict(prior, timestamp=timestamp)
```

### Behavioral contract

- **Input:** a `GaussianState` carrying a `timestamp` (datetime); a target
  `timestamp >= prior.timestamp`.
- **Output:** a `GaussianStatePrediction` at the target time. Mean propagates as
  `x' = F(dt).x` (positions advance by `v.dt`, velocities unchanged); covariance
  grows by `F.P.F^T + Q(dt)`.
- **dt = 0** -> returns the prior mean unchanged with covariance unchanged
  (identity transition). Must not error.
- **Stateless across calls** — owns only the model, never a track. The tracker
  holds track state; the predictor is shared/reusable across all tracks and
  threads of execution.
- **Determinism** — prediction is pure linear algebra (no RNG). Identical inputs
  -> identical outputs. (Unlike the radar, which draws process noise; the
  *filter's* predict is noiseless.)

### Tuning knobs

- `process_noise` (q): the only constructor parameter. The radar uses `1.0`; the
  filter's q should be **>=** the radar's truth q (the filter must allow for
  un-modeled maneuvers the truth model doesn't have). Recommend exposing via
  `ScenarioConfig` later (`tracker.process_noise`).
- Measurement noise R lives on the **updater/measurement model**, not here —
  explicitly out of scope for the predictor.

### Acceptance tests (`gs/tests/test_predictor.py`)

1. **Straight-line propagation:** prior at `[0, 10, 0, 0, 100, 0]`, near-zero
   covariance, predict +5 s -> mean position `~= (50, 0, 100)`, velocity
   unchanged. (CV kinematics.)
2. **Covariance grows:** `trace(P_pred) > trace(P_prior)` for dt > 0, and grows
   monotonically with dt.
3. **Zero dt is identity:** predicting to the prior's own timestamp returns the
   prior mean and covariance unchanged.
4. **Backwards-time guard:** target time < prior time raises `ValueError`.
5. **Shared model identity:** `predictor.transition_model is` the model later
   handed to the updater (so the filter is internally consistent).

### Explicitly out of scope for this component

Data association, gating, track lifecycle, the measurement model, and maneuver
models (ConstantTurn / IMM-analogue). Those land in Phases 2-3.

---

## Open questions before building (low-stakes, with defaults)

1. **Covariance ordering in the `Track` message** — publish the 6x6 in native
   Stone Soup `[x,vx,y,vy,z,vz]` order (default, just documented) or reorder to
   `[x,y,z,vx,vy,vz]` to line up with the separated `position`/`velocity`
   tuples? Reordering is consumer-friendly but error-prone.
   *Default: native order + a docstring note.*
2. **Tracker cadence** — tick on every detection, or on a fixed timer (e.g.
   10 Hz)? *Default: fixed timer driven by the node loop, since detections
   arrive per-target and we want one coherent multi-target update per tick.*
3. **Should `GS_TRACKS` be `SecureContract.seal`'d?** It crosses the
   Team-2->Team-3 boundary. *Default: publish plaintext for the MVP (matches how
   radar publishes today), add sealing as a follow-up once the pipeline works
   end-to-end.*

---

## Appendix: relevant SOTA context (from deep research)

Stone Soup ships the full filter family — `KalmanPredictor` (linear),
`ExtendedKalmanPredictor` (EKF), `UnscentedKalmanPredictor` (UKF),
`SqrtKalmanPredictor` (square-root), `CubatureKalmanPredictor` (CKF) — plus
multiple-model particle predictors (`MultiModelPredictor`,
`RaoBlackwellisedMultiModelPredictor`) as an IMM analogue. Multi-target
association: `GNNWith2DAssignment` (GNN), `JPDA` (+ `JPDAwithEHM`/`EHM2` via the
PyEHM plugin, `JPDAwithLBP`), MHT-style `MFADataAssociator`/`MFAHypothesiser`,
and the RFS `PHDUpdater` (GM-PHD). For multiple maneuvering Shaheds with noisy
position detections + clutter: start with linear KF + GNN, escalate to JPDA or
GM-PHD as clutter density rises.
