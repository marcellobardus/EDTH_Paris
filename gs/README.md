# `gs` — Ground Station (Team 2)

The ground station is the **brain** of the air-defence pipeline. It sits
downstream of the radar and turns a stream of noisy, anonymous radar hits into a
coherent, stable picture of every Shahed in the air — then (eventually) scores
those threats and assigns interceptors to them.

It is **not** a radar and never sees drones directly. It only consumes what the
radar reports (`RadarDetection`s) and reasons about it.

```
            /radar/detections                 /gs/tracks            /gs/threats        /gs/assignments
radar ───────────────────────►  GROUND STATION  ──────────►  ( ──────────►  ──────────► )
        noisy position hits     ┌─────────────┐   clean       threat scoring   interceptor
                                │ track fusion │   tracks      (planned)        assignment
                                │   (Kalman)   │                                (planned)
                                └─────────────┘
```

## Responsibilities

| Responsibility | Status | Where |
|---|---|---|
| **Track fusion** — fuse noisy detections into clean, identity-stable tracks (multi-target Kalman tracking) | ✅ implemented | `tracker.py`, `filter.py`, `predictor.py`, `track_publisher.py` |
| **Threat scoring** — score each track (`threat_score`, `eta_seconds`) → `/gs/threats` | ⬜ planned | — |
| **Assignment** — Hungarian optimizer pairing interceptors to threats → `/gs/assignments` | ⬜ planned (mock exists) | `mock_assignments.py` |

The current node publishes fused **tracks** on `/gs/tracks`. Threat scoring and
assignment are the next layers; `mock_assignments.py` is a placeholder publisher
so downstream teams can develop against synthetic assignments meanwhile.

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
- **`gs_node.py`** — the runnable CLI. Wires a ZeroMQ transport to the
  `TrackPublisher`.
- **`launch_decider.py`** — *legacy placeholder* (naive nearest-neighbour launch
  decision). Superseded by the tracker; kept for its tests.

## Inputs / Outputs

| | Topic | Message | Direction |
|---|---|---|---|
| **In** | `/radar/detections` | `RadarDetection` | from radar |
| **Out** | `/gs/tracks` | `Track` | to downstream |
| (planned) | `/gs/threats` | `ThreatAssessment` | to assignment |
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

You'll see the GS log fused tracks per scan, e.g.:

```
14:08:16.237 | gs | scan t=  12.1s  tracks[5]: e9099ffa@(-2339,768,94), 311e8789@(-262,-1458,236), ...
```

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
| `--tracks-addr` | `tcp://127.0.0.1:5557` | tracks PUB address |
| `--rate` | `10.0` | tracker tick rate (Hz) |

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

## Testing

```bash
uv run pytest gs/            # all gs tests
uv run pytest gs/tests/test_tracker.py   # one file
```

Coverage: unit tests for the predictor and filter (incl. RMSE-beats-raw-noise),
integration tests for the full radar→tracker path (3 stable tracks, ID
persistence, clutter rejection, stale-track deletion, intermittent detection,
close crossing).

## Dependencies

`contracts` (workspace), `numpy`, `scipy`, `stonesoup`. Managed via `uv` from the
repo root (`uv sync`).

## See also

- `KALMAN_TRACKER_PLAN.md` — the design/spec the tracker was built from, plus
  SOTA context.
- `contracts/` — shared message types, topic names, config schema (the contract
  every team depends on).
