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

All inter-process communication is ROS2 pub/sub with `network_mode: host` in Docker (required for DDS). Topic names must always be imported from `contracts/contracts/topics.py` — never hardcoded.

**Pre-launch flow** (GS active):
```
/radar/detections → /gs/tracks → /gs/threats → /gs/assignments → interceptors
```

**In-flight flow** (Situation B, GS role ends at launch):
```
/interceptors/{id}/state  (5 Hz broadcast to all peers)
/interceptors/{id}/claim  (during re-tasking)
/interceptors/{id}/commit (after consensus)
/interceptors/{id}/waypoint → simulation (10 Hz, PN pursuit point)
```

### Contracts Module (`contracts/`)

Three files — import from here only:
- `messages.py` — all dataclasses: `RadarDetection`, `Track`, `ThreatAssessment`, `Assignment`, `InterceptorState`, `Claim`, `Commit`, `WaypointCommand`
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

**Claim-and-Confirm** (`agent/`): interceptor broadcasts `Claim`, waits 400 ms for competing claims, yields to higher `interceptor_id`, then broadcasts `Commit`. Falls back to greedy (closest uncovered track) after 2 failed rounds or under sustained packet loss.

### Scenario Config

Edit `config/scenario_default.yaml` to change scenario parameters. The Pydantic schema in `contracts/contracts/config.py` validates on load — no code changes needed to reconfigure.

### Independent Development / Mocking

Each team can develop against mocks without waiting for other teams:
- **Team 2** (GS) uses `sim/mock_radar.py` to get synthetic `/radar/detections`
- **Team 3** (Agent) uses `gs/mock_assignments.py` to get synthetic `/gs/assignments`
- The `INTERCEPTOR_ID` environment variable (e.g. `i1`) identifies an agent process; set it per container in `docker-compose.yml`

### Docker

`docker/base.Dockerfile` provides the shared base: ROS2 Jazzy + Gazebo Harmonic + `uv`. Each module's `Dockerfile` builds on top of it. The `base` service must be built first (`depends_on: [base]` in compose).
