# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EDTH Paris Hackathon — **Real-Time Multi-Interceptor Coordination**. The system simulates and compares two scenarios:
- **Situation A** — interceptors fly fixed pre-launch assignments with no communication
- **Situation B** — interceptors share state peer-to-peer mid-flight and re-assign themselves using a claim-and-confirm consensus protocol

The goal is to show Situation B achieves a measurably higher threat-neutralization rate.

## Common Commands

```bash
# Install all workspace packages (run from repo root)
uv sync

# Lint
uv run ruff check .
uv run ruff format .

# Type check
uv run mypy .

# Run tests
uv run pytest

# Run a single test file
uv run pytest sim/tests/test_radar.py

# Build all Docker images
docker compose build

# Run the full stack
docker compose up

# Run only specific services
docker compose up sim gs agent viz
```

## Architecture

### Monorepo Layout

`uv` workspace with one package per team. **`contracts/` is the shared foundation** — all other packages depend on it. Any change to `contracts/` is a breaking change requiring team consensus.

| Package | Team | Role |
|---|---|---|
| `contracts/` | Team 4 | Shared message types, topic names, config schema |
| `sim/` | Team 1 | Gazebo world, Shahed agents, radar sensor, engagement detection |
| `gs/` | Team 2 | Track fusion (Kalman), threat scoring, Hungarian assignment optimizer |
| `agent/` | Team 3 | Interceptor: PN guidance, peer comms, claim-and-confirm re-tasking |
| `viz/` | Team 4 | Gazebo overlays, metrics dashboard, CSV logger |

### Communication

All inter-process communication is ROS2 pub/sub with **both `network_mode: host` and `ipc: host`** in Docker (see Gotchas — without `ipc: host` Fast DDS silently drops every cross-container sample). Topic names must always be imported from `contracts/contracts/topics.py` — never hardcoded.

**Pre-launch flow** (GS active):
```
/radar/detections → /gs/tracks → /gs/threats → /gs/assignments → interceptors
```

**In-flight flow** (Situation B, GS role ends at launch):
```
/interceptors/{id}/state  (5 Hz broadcast to all peers — carries ownership + priority key + lock)
/interceptors/{id}/waypoint → simulation (10 Hz, PN pursuit point)
```
Re-tasking is **CBAA** (consensus-by-broadcast): there is no claim/commit
channel. The single `/state` message *is* the protocol — see "Re-tasking" below.

### Contracts Module (`contracts/`)

Three files — import from here only:
- `messages.py` — all dataclasses: `RadarDetection`, `Track`, `ThreatAssessment`, `Assignment`, `InterceptorState` (extended for CBAA: `owns_priority`, `locked`, `seq`), `WaypointCommand`
- `topics.py` — `Topics` class with static attributes and helper methods (`Topics.interceptor_state("i1")`)
- `config.py` — `ScenarioConfig` Pydantic model; load with `ScenarioConfig.from_yaml("config/scenario_default.yaml")`

All positions are `(x, y, z)` in metres. All timestamps are `float` seconds since scenario start.

### Key Algorithms

**Assignment Optimizer** (`gs/`): Hungarian algorithm via `scipy.optimize.linear_sum_assignment`. Cost matrix: `C[i][j] = intercept_time / threat_score`; infeasible pairs (out of range) get cost `1e9`.

**Proportional Navigation** (`agent/`): guidance update every 100 ms.
```python
omega = cross(R, R_dot) / dot(R, R)   # LOS angular rate
a_cmd = N * self_speed * omega         # N ≈ 3–5
```

**Re-tasking — CBAA, stale-safe** (`agent/retasking.py`): no claim/commit, no consensus window. Each interceptor broadcasts which track it `owns` plus a self-computed **priority key** `(affinity_bucket, danger, id_rank)` (lexicographic, larger wins). Ownership is decided by a total order on that key, so it converges by re-broadcast instead of by rounds:
- `affinity_bucket = frozen_threat / (intercept_bucket + 1)` — the intercept time is *bucketed with hysteresis* so estimate jitter never reorders the key.
- Peers **never recompute** a peer's key; they compare the transmitted `owns_priority` directly.
- **Monotone lock**: once `intercept_time < lock_threshold` the owner locks and never yields (terminal guidance is uninterruptible).
- **Stale-safe**: a silent peer keeps covering its last track until `silence_timeout` (then awareness frees it); an out-of-order packet (lower `seq`) is dropped.
- **Loss robustness**: a changed state is re-emitted `change_repeat`× and a heartbeat goes out every cycle, so peers reconverge after drops.

Tunables live in the `retasking:` block of the scenario YAML (`RetaskingConfig`).

### Scenario Config

Edit `config/scenario_default.yaml` to change scenario parameters. The Pydantic schema in `contracts/contracts/config.py` validates on load — no code changes needed to reconfigure.

### Independent Development / Mocking

Each team can develop against mocks without waiting for other teams:
- **Team 2** (GS) uses `sim/mock_radar.py` to get synthetic `/radar/detections`
- **Team 3** (Agent) uses `gs/mock_assignments.py` to get synthetic `/gs/assignments`
- The `INTERCEPTOR_ID` environment variable (e.g. `i1`) identifies an agent process; set it per container in `docker-compose.yml`

### Docker

`docker/base.Dockerfile` provides the shared base: ROS2 Jazzy + Gazebo Harmonic + `uv`. Each module's `Dockerfile` builds on top of it. The `base` service must be built first (`depends_on: [base]` in compose).

### Sim ↔ Gazebo wiring (`sim/`)

Two processes back the simulation:
- **`sim.world`** launches Gazebo headless (`gz sim -s -r`) on the `intercept_scenario.sdf` world plus the `gz-launch` websocket server on `:9002` (consumed by gzweb at `:8080`). It only *visualizes* — it closes no control loop.
- **`sim.driver`** is the authority that closes the loop. It owns the Shahed kinematics in pure Python, and flies the interceptors through **real Gazebo physics**: a `GzBridge` (using `gz.transport13` + `gz.msgs10`) publishes `enable` + body-frame `cmd_vel` toward each agent waypoint and reads true interceptor poses back from `/world/intercept_scenario/pose/info`. Shaheds are `<static>` SDF models (no controller) and are teleported each tick via the `set_pose_vector` service. The driver also emits `/simulation/ground_truth`, `/radar/detections`, `/simulation/engagement`, and (until the real GS lands) perfect-sensor `/gs/tracks` + a one-shot `/gs/assignments` (`--no-gs` disables the GS stand-in).

ID bridge: agent id `i{n}` ↔ gz model `interceptor_{n}`; track id `t{n}` ↔ gz model `shahed_{n}`.

## Gotchas (hard-won — read before debugging "nothing moves")

- **DDS needs `ipc: host`, not just `network_mode: host`.** Fast DDS (the Jazzy default RMW) discovers peers over UDP — so `ros2 topic info` shows publishers/subscribers matched — then picks the shared-memory transport for same-host peers and **silently drops every sample** across containers, because each container has its own `/dev/shm`. Symptom: agents never receive `/gs/assignments`/`/gs/tracks`/ground-truth and sit frozen at launch. Fix: `ipc: host` on every DDS service (it's a create-time option — `docker compose up -d` to apply, a plain `restart` won't). `gzweb` is exempt (it uses the websocket, not DDS).
- **Read interceptor poses from `/world/intercept_scenario/pose/info`, NEVER `/model/interceptor_N/odometry`.** The odometry topic does not report true world position — it reads as frozen at the spawn point even while the model flies. This one wrong topic can make working flight look completely broken.
- **The multicopter controller can't dash at the config's nominal speeds.** A 3 kg quad under `MulticopterVelocityControl` flies stably only to ~11–13 m/s; commanding more (or stepping velocity instantly) tips it over. The scenario is tuned to this envelope (shaheds 5–8 m/s, interceptors 13 m/s). `cmd_vel` linear velocity is **body-frame** — the driver rotates the desired world velocity by −yaw.
- **SDF param names are unforgiving.** It is `maxLinearAcceleration` (NOT `maximumLinearAcceleration`, which gz silently ignores → unlimited accel → flips) and `maximumLinearVelocity` for the speed cap. Both live in `intercept_scenario.sdf`.
- **gz service requests are blocking and run on the rclpy executor thread.** Issuing several per tick starves the physics step (Shaheds barely move). Batch them: one `set_pose_vector` (`gz.msgs.Pose_V`) call for all Shaheds, on its own lower-rate timer.
- **`cmd_vel` actuates only when enabled AND a twist has been received** (`controllerActive && cmdVelMsg.has_value()`). The driver re-publishes `enable=true` every tick so a late-discovering controller still latches.
