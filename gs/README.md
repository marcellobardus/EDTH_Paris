# `gs` — Ground Station (Team 2)

The ground station is the **brain** of the air-defence pipeline. It sits
downstream of the radar and turns a stream of noisy, anonymous radar hits into a
coherent, stable picture of every Shahed in the air, **scores how dangerous each
one is**, and then (eventually) assigns interceptors to them.

It is **not** a radar and never sees drones directly. It only consumes what the
radar reports (`RadarDetection`s) and reasons about it.

```
        /radar/detections          /gs/tracks         /gs/threats        /gs/assignments
radar ──────────────────►  GROUND STATION  ─────────────────────────►  ( ──────────► )
   noisy position hits   ┌───────────────────────────────┐  scored        interceptor
                         │ track fusion → threat scoring  │  tracks        assignment
                         │   (Kalman)       (eta / score) │                (planned)
                         └───────────────────────────────┘
```

## Responsibilities

| Responsibility | Status | Where |
|---|---|---|
| **Track fusion** — fuse noisy detections into clean, identity-stable tracks (multi-target Kalman tracking) | ✅ implemented | `tracker.py`, `filter.py`, `predictor.py`, `track_publisher.py` |
| **Threat scoring** — score each track (`threat_score`, `eta_seconds`) → `/gs/threats` | ✅ implemented | `threat_assessor.py` |
| **Assignment** — Hungarian optimizer pairing interceptors to threats → `/gs/assignments` | ⬜ planned (mock exists) | `mock_assignments.py` |

The node publishes fused **tracks** on `/gs/tracks` and their **threat scores**
on `/gs/threats`. Assignment is the next layer; `mock_assignments.py` is a
placeholder publisher so downstream teams can develop against synthetic
assignments meanwhile.

## Architecture

The tracker is built from [Stone Soup](https://stonesoup.readthedocs.io)
components, layered so each piece is independently testable:

```
RadarDetection (per scan)
        │
        ▼
TrackPublisher            bus ⇄ tracker bridge: buffers detections, ticks once
 (track_publisher.py)     per scan, publishes contracts.Track on /gs/tracks
        │
        ▼
MultiTargetTracker        per-scan loop: associate → update/coast → delete → initiate
 (tracker.py)             • DistanceHypothesiser (Mahalanobis gate)
        │                 • GNNWith2DAssignment (data association)
        │                 • MultiMeasurementInitiator (M-of-N confirmation)
        │                 • CovarianceBasedDeleter (drop stale/coasted tracks)
        ▼
SingleTargetFilter        predict → update for one track
 (filter.py)              • KalmanUpdater + position-only LinearGaussian (noise R)
        │
        ▼
ConstantVelocityPredictor predict-only: project a track's state forward in time
 (predictor.py)           • KalmanPredictor + constant-velocity transition (noise Q)
```

**State convention:** 6-D `[x, vx, y, vy, z, vz]` (metres, m/s), matching the
radar simulator so the filter is well-matched to the data. Timestamps are
`float` seconds since scenario start; the tracker converts them to Stone Soup's
absolute `datetime` clock via a `start_time` anchor.

### Threat scoring

Once a track is fused, `ThreatAssessor` (`threat_assessor.py`) scores how
dangerous it is — a **pure, stateless** map `Track → ThreatAssessment`. Scoring
is per-track and independent (no cross-track coupling like tracking has), so it's
a simple function of one track plus the defended-asset position.

The model is **asset-defence by closing speed**: a drone is dangerous to the
extent it is *closing* on the defended point. From the track's straight-line,
constant-velocity extrapolation:

```
to_target     = asset − track.position
distance      = ‖to_target‖
closing_speed = (track.velocity · to_target) / distance     # radial speed toward the asset
```

- **`eta_seconds`** (time to impact):
  - `distance / closing_speed` when `closing_speed > min_closing_speed` (genuinely inbound),
  - `ETA_SENTINEL` (`1e9`) when receding or merely crossing (not closing),
  - `0` when already at the asset.
- **`threat_score`** (higher = more dangerous) = `1 / max(eta, EPS_ETA)` — so the
  most *imminent* threat scores highest. A non-closing track gets a tiny but
  **strictly positive** score.

Two deliberate details:

- **`ETA_SENTINEL` is a large finite number, not `inf`** — so it survives the
  bus's JSON serialisation and mirrors the assignment's `1e9` infeasibility
  convention.
- **`threat_score` is always `> 0`** — the assignment cost is
  `C = intercept_time / threat_score`, so a zero score would divide by zero; the
  floor keeps it finite while still ranking non-threats last.

The node scores every track each tick and publishes the `ThreatAssessment` on
`/gs/threats`, which rides the **same outbound PUB socket** as `/gs/tracks` (a
PUB socket carries many topics; subscribers filter). The defended asset defaults
to the **origin** — where the mock world flies the drones and the visualizer
draws the asset — and is overridable with `--target X Y Z`. *(Note:
`ScenarioConfig.target_position` is `[500,500,0]`; the running mock system uses
the origin, so they currently differ — align them when wiring real scenarios.)*

Out of scope for v1 (the interface doesn't change if added later): altitude
weighting, time-to-*closest-approach* instead of time-to-impact, and
covariance/confidence weighting. See `THREAT_ASSESSOR_PLAN.md`.

### Modules

- **`predictor.py`** — `ConstantVelocityPredictor`. Wraps Stone Soup's
  `KalmanPredictor` with a constant-velocity transition model. Predict-only.
- **`filter.py`** — `SingleTargetFilter`. Pairs the predictor with a
  `KalmanUpdater` + position-only measurement model. Stateless: `initiate()`
  seeds a track from one detection, `update()` runs predict→update.
- **`tracker.py`** — `MultiTargetTracker`. The multi-target orchestrator
  (manual per-scan loop, since the repo bus is push-based). Maps Stone Soup
  tracks → `contracts.messages.Track`.
- **`track_publisher.py`** — `TrackPublisher`. Bridges the bus to the tracker:
  buffers incoming detections, ticks once per scan, publishes tracks. A tick
  with no buffered detections is a no-op (the tracker advances only on real
  scans).
- **`threat_assessor.py`** — `ThreatAssessor`. Pure, stateless `Track →
  ThreatAssessment` scorer (closing-speed → eta → `1/eta`). See above.
- **`gs_node.py`** — the runnable CLI. Wires a ZeroMQ transport to the
  `TrackPublisher`, scores each track via `ThreatAssessor`, and publishes both
  `/gs/tracks` and `/gs/threats`.
- **`launch_decider.py`** — *legacy placeholder* (naive nearest-neighbour launch
  decision). Superseded by the tracker; kept for its tests.

## Inputs / Outputs

| | Topic | Message | Direction |
|---|---|---|---|
| **In** | `/radar/detections` | `RadarDetection` | from radar |
| **Out** | `/gs/tracks` | `Track` | to downstream |
| **Out** | `/gs/threats` | `ThreatAssessment` | to assignment |
| (planned) | `/gs/assignments` | `Assignment` | to interceptors |

Topics and message types come from `contracts/` — never hardcode them.

## How to run

The ground station binds two ZeroMQ endpoints: SUB on `--addr` (detections in)
and PUB on `--tracks-addr` (tracks out). **Start it first** (it's the stable
endpoint), then the radar, then the world.

```bash
# Terminal 1 — ground station (binds :5556 detections-in, :5557 tracks-out)
uv run python -m gs.gs_node

# Terminal 2 — radar sensor (subscribes to ground truth, emits noisy detections)
uv run python -m sim.radar_sensor_node

# Terminal 3 — mock world (random drones; throwaway stand-in for Jules' Gazebo)
uv run python -m sim.world_node --transport zmq
```

For a livelier demo: `... sim.world_node --transport zmq --step-interval 0.5 --min 2 --max 4 --seed 1`.

You'll see the GS log threats per scan, ranked by score (highest first):

```
15:09:12.402 | gs | scan t=  10.1s  threats[4]: bb9ff0c5 score=0.031 eta=32s, c77073b1 score=0.026 eta=38s, 444539ab score=0.021 eta=48s, adf49bf5 score=0.017 eta=60s
```

The visualizer (`viz/track_viewer.py`) subscribes to both `/gs/tracks` and
`/gs/threats` and draws each target's threat weight — a red halo and marker that
grow with the score, plus a `thr`/`eta` label.

### Quick all-in-one alternative

`sim.radar_node` bundles a synthetic world *and* the radar into one process —
handy for headless runs without the split:

```bash
uv run python -m gs.gs_node                       # terminal 1
uv run python -m sim.radar_node --transport zmq   # terminal 2
```

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--addr` | `tcp://127.0.0.1:5556` | detections SUB address |
| `--tracks-addr` | `tcp://127.0.0.1:5557` | tracks + threats PUB address |
| `--rate` | `10.0` | tracker tick rate (Hz) |
| `--target X Y Z` | `0 0 0` | defended-asset position for threat scoring |

## Tuning

Tracker knobs (defaults in `MultiTargetTracker`, tuned for the demo scenario —
re-tune for real sensor characteristics):

| Knob | Default | Effect |
|---|---|---|
| `process_noise` (Q) | `1.0` | larger → trusts the motion model less, adapts faster to manoeuvres, noisier |
| `measurement_noise_m` (R) | `5.0` | per-axis position measurement std (match the radar) |
| `gate_distance` | `4.0` | Mahalanobis association gate (deliberately above the tutorial's 3.0 — a 3σ gate over 3-D noise occasionally spawned duplicate tracks) |
| `min_init_points` | `2` | M-of-N confirmation: detections needed before a track is confirmed (suppresses clutter) |
| `covar_trace_thresh` | `1000.0` | a coasting (undetected) track is deleted once its covariance trace exceeds this (≈ 5–6 missed scans) |

Threat-scoring knobs (`ThreatAssessor`):

| Knob | Default | Effect |
|---|---|---|
| `target_position` | `(0,0,0)` (via `--target`) | the defended asset; eta/score are measured against this |
| `min_closing_speed` | `1.0` | radial speed (m/s) below which a track counts as not inbound → sentinel eta, ~0 score |

## Testing

```bash
uv run pytest gs/            # all gs tests
uv run pytest gs/tests/test_tracker.py   # one file
```

Coverage: unit tests for the predictor and filter (incl. RMSE-beats-raw-noise),
integration tests for the full radar→tracker path (3 stable tracks, ID
persistence, clutter rejection, stale-track deletion, intermittent detection,
close crossing), and unit tests for the threat assessor (eta/score for inbound,
monotonicity, receding/crossing → sentinel, at-asset max, divide-by-zero safety).

## Dependencies

`contracts` (workspace), `numpy`, `scipy`, `stonesoup`. Managed via `uv` from the
repo root (`uv sync`).

## See also

- `KALMAN_TRACKER_PLAN.md` — the design/spec the tracker was built from, plus
  SOTA context.
- `THREAT_ASSESSOR_PLAN.md` — the design/spec for the threat scorer.
- `contracts/` — shared message types, topic names, config schema (the contract
  every team depends on).
