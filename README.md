# Real-Time Multi-Interceptor Coordination

**EDTH Paris Hackathon — Challenge 2 (Alta Ares)**
**Venue:** 5 Rue La Boétie, 75008 Paris · **Dates:** June 12–14, 2026

---

## What this project does

This system demonstrates that interceptors capable of mid-flight peer-to-peer communication achieve a measurably higher threat-neutralization rate than interceptors flying fixed pre-launch assignments.

The simulation runs two scenarios against the same random seed:

| | Situation A | Situation B |
|---|---|---|
| **Assignment** | Fixed at launch, never updated | Updated mid-flight via consensus |
| **Communication** | None after launch | Peer-to-peer state broadcast at 5 Hz |
| **Failure modes** | Dead-target redundancy, convergence on proximate targets | Both detected and corrected onboard |

A run is successful when Situation B shows fewer threats reaching the target, fewer wasted munitions, and fewer convergence failures than Situation A.

---

## Architecture

```
sim/         Gazebo world, Shahed drones, radar sensor, engagement detection
gs/          Track fusion (Kalman), threat scoring, Hungarian assignment optimizer
agent/       One process per interceptor: PN guidance, comms, re-tasking
viz/         ROS2→HTTP bridge + web dashboard (live tactical picture)
contracts/   Shared message types, topic names, config schema (all teams depend on this)
```

**Pre-launch** — the ground station fuses radar detections into tracks, scores threats, and issues assignments via the Hungarian algorithm. Its role ends at launch.

**In-flight (Situation B)** — each interceptor broadcasts state at 5 Hz. When a coverage conflict is detected (two interceptors converging on the same target, or an uncovered track), the interceptor runs a claim-and-confirm consensus: it broadcasts a `Claim`, waits 400 ms for competing claims, yields to the higher-ID interceptor, then broadcasts `Commit`. Falls back to greedy assignment after 2 failed rounds.

All communication is ROS2 pub/sub. Topic names are defined in `contracts/contracts/topics.py`.

The simulation is the **ground truth**: the `sim.driver` owns the Shahed kinematics in pure Python and flies the interceptors through real Gazebo physics. Everything the dashboard shows is sourced from the sim — nothing is fabricated except the interceptors' weapon/ammo cosmetics (see *Known limits*).

---

## The web dashboard

A live tactical picture (radar-style top-down map + side panels) served in the browser.

```
sim / gs / agents  ──ROS2──▶  viz.bridge  ──HTTP /api──▶  dashboard (Vite)
   (ground truth)              :8000                       :5173
```

`viz.bridge` subscribes to the live ROS2 bus, keeps the latest snapshot in memory, and serves it as a small REST API. The dashboard (vanilla TS + HTML canvas) polls that API at 5 Hz and renders it. A self-contained in-browser **mock** is the default so the frontend runs with no backend; pass `?real` in the URL to switch to live sim data.

### What it maps (all from the sim config / live bus)

| Entity | Source | Drawn as |
|---|---|---|
| **Defended site** | `scenario.target_position` | Centre dish + dashed footprint |
| **Ground station** | `interceptors.launch_position` | Square launch marker (distinct from the site) |
| **Radars** | `radars[]` (position + range) | Sensor glyph + coverage ring |
| **Shaheds** (threats) | `/gs/tracks` → fallback `/simulation/ground_truth` | Hostile air diamond + trail + velocity vector |
| **Interceptors** | `/interceptors/{id}/state` | Friendly effector frame + range ring |
| **Assignments** | `/gs/assignments` + live re-tasking | Dashed engagement lines |
| **Engagements** | `/simulation/engagement` | Kill bursts + event log |

### Threat panel (left)

Contacts are ranked by **imminence** — lowest time-to-impact (ETA) on top. Each row carries a **threat level** badge derived from ETA:

| Level | ETA |
|---|---|
| `CRITICAL` | < 10 s |
| `HIGH` | < 25 s |
| `MEDIUM` | < 60 s |
| `LOW` | ≥ 60 s |

…and the full telemetry the sim publishes: `ETA`, `SPD` (3D speed), `ALT` (altitude), `BRG` (bearing from the site), `RNG` (range).

### Interceptor cards (right)

Per-effector live telemetry from each agent's state broadcast: `Speed`, `Alt`, `Hdg` (heading), `Target`, plus `Range` (config) and `Pk` (geometric kill probability).

### REST API (`viz.bridge`, port 8000)

| Method | Endpoint | Returns |
|---|---|---|
| GET | `/api/scenario` | Static geometry: defended site, ground station, radars |
| GET | `/api/tracks` | Live shaheds (fused tracks, or ground-truth fallback) |
| GET | `/api/threats` | Threat score + ETA per contact |
| GET | `/api/assignments` | Live interceptor↔track pairing (reflects re-tasking) |
| GET | `/api/engagement-events` | Kill/miss log |
| GET | `/api/interceptors/{id}/state` | Position, velocity, target, status |
| GET | `/api/health` | Bridge + sim status |
| POST | `/api/sim/{start,stop,reset}` | Drive the sim (only when `VIZ_SPAWN_SIM=1`) |

---

## Ports

| Port | Service | What it is |
|---|---|---|
| `5173` | dashboard (Vite dev server) | **Open this** — the tactical picture |
| `8000` | `viz.bridge` | ROS2→HTTP REST API the dashboard polls |
| `8080` | `gzweb` | Optional Gazebo 3D scene view |
| `9002` | `gz-sim` websocket | Raw Gazebo pose feed (consumed by gzweb) |

---

## How to use

### Prerequisites

- **Docker** + **docker compose** (the sim stack runs ROS2 Jazzy + Gazebo Harmonic in containers — there is no host ROS install)
- **Node.js ≥ 20** + npm (for the dashboard dev server)
- **uv** + Python 3.12 (only for host-side lint/test; not needed to run the stack)

> ⚠️ The Python virtualenv (`.venv`) is **Python 3.13 and has no `rclpy`** — do **not** run `python -m viz.bridge` on the host, it cannot reach the ROS bus. The bridge only works inside Docker.

### 1 — Build the images (first time only)

```bash
docker compose build
```

### 2 — Launch the full stack

```bash
docker compose up -d
```

This starts: `sim` (Gazebo), `driver` (scenario authority), `gs` (ground station), `agent1..3` (interceptors), `viz` + `viz-api` (bridge on :8000), `gzweb` (:8080).

> ⚠️ **Always use `up -d` (not `restart`) after touching `docker-compose.yml`.** Several services need `ipc: host` so Fast DDS can deliver samples across containers; `ipc` is a **create-time** option that `docker compose restart` silently ignores. Symptom of a missed `ipc: host`: the dashboard shows the map geometry but no moving shaheds/interceptors, and interceptors sit frozen at launch.

Verify the bridge sees live data:

```bash
curl -s localhost:8000/api/health
curl -s localhost:8000/api/tracks      # should be a non-empty array shortly after launch
```

### 3 — Start the dashboard

```bash
cd viz/dashboard
npm install          # first time only
npm run dev          # serves on http://localhost:5173 (proxies /api → :8000)
```

### 4 — Open it

| URL | Mode |
|---|---|
| **http://localhost:5173/?real** | **Live sim data** (this is what you want) |
| http://localhost:5173/?mock | In-browser mock, no backend needed |
| http://localhost:5173/ | Defaults to mock |

Optional: http://localhost:8080 for the Gazebo 3D scene.

### 5 — Replay the scenario

The scenario runs once for `duration_max` seconds (240 s by default) then stops; the map empties as shaheds are neutralised or reach the target. To replay against the live stack (where the dashboard Run button is disabled because `VIZ_SPAWN_SIM=0`):

```bash
docker compose up -d --force-recreate driver gs agent1 agent2 agent3
```

### Situation A vs B

Edit `config/scenario_default.yaml`, set `situation: A` (or `B`), then recreate the stack:

```bash
docker compose up -d --force-recreate
```

---

## Configuration

All scenario parameters live in `config/scenario_default.yaml` — no code changes needed to reconfigure. The schema is validated by Pydantic on load (`contracts/contracts/config.py`).

```yaml
scenario:
  seed: 42
  target_position: [500, 500, 0]
  duration_max: 240         # seconds
  situation: B              # A or B

radars:
  - position: [100, 100, 10]
    range: 800
  - position: [400, 200, 10]
    range: 600

shaheds:
  count: 4
  speed_mps: [5, 8]         # tuned to the multicopter flight envelope
  spawn_radius: 700

interceptors:
  count: 3
  speed_mps: 13             # stable ceiling for a 3 kg quad under velocity control
  range_m: 700
  launch_position: [480, 480, 0]

comms:
  packet_loss_prob: 0.10
  consensus_window_ms: 400
```

> The speeds are deliberately low: the Gazebo multicopter controller flies stably only to ~11–13 m/s. See `CLAUDE.md` → *Gotchas* before retuning.

---

## Development

```bash
uv sync                    # install workspace (Python 3.12, uv required)

uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy .              # type check
uv run pytest              # all tests
uv run pytest path/to/test.py   # single test file
```

Dashboard:

```bash
cd viz/dashboard
npm run build              # tsc type-check + production bundle
```

Each team can develop independently using mock publishers — see `docs/workstreams.md`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Map geometry shows but nothing moves | A DDS service is missing `ipc: host` (often after `restart`) | `docker compose up -d --force-recreate` |
| `/api/tracks` returns `[]` | Scenario finished, or sim not yet discovered | Replay (step 5); wait ~10 s after launch for DDS discovery |
| Dashboard shows fake/looping data | Opened without `?real` (mock mode) | Use `http://localhost:5173/?real` |
| `ModuleNotFoundError: No module named 'rclpy'` | Ran the bridge on the host venv | Run it in Docker (`viz-api`), never on the host |
| `address already in use :8000` | `viz-api` container already serving | That's expected — it's the bridge |
| Interceptors freeze at launch, all shaheds leak | Agents never received assignments (DDS dropped) | Confirm every DDS container is `ipc: host`: `docker inspect <c> --format '{{.HostConfig.IpcMode}}'` |

---

## Known limits

The sim models position, velocity, assignment, and engagement — but **not** weapon type, magazine, or reload. The interceptor name (`SABER`/`TALON`/…), ammo bar, and reload timer on the cards are cosmetic. `Range` (config) and `Pk` (geometric) are real. Wiring real armament state would mean extending `InterceptorState` in `contracts/` — a contract change requiring team consensus.

---

## Docs

- [`docs/specification.md`](docs/specification.md) — full functional and non-functional requirements
- [`docs/architecture.md`](docs/architecture.md) — component map, topic table, algorithm details, module breakdown
- [`docs/workstreams.md`](docs/workstreams.md) — team ownership, milestones, mocking strategy, integration timeline
- [`CLAUDE.md`](CLAUDE.md) — hard-won gotchas (DDS, Gazebo flight envelope, SDF params)
