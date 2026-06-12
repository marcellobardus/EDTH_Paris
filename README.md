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
PC 1 — sim/          Gazebo world, Shahed drones, radar sensor, engagement detection
PC 2 — gs/           Track fusion (Kalman), threat scoring, Hungarian assignment optimizer
         agent/       One process per interceptor: PN guidance, comms, re-tasking
         viz/         Gazebo overlays, metrics dashboard, CSV logger
         contracts/   Shared message types, topic names, config schema (all teams depend on this)
```

**Pre-launch** — ground station fuses radar detections into tracks, scores threats, and issues assignments via the Hungarian algorithm. Its role ends at launch.

**In-flight (Situation B)** — each interceptor broadcasts state at 5 Hz. When a coverage conflict is detected (two interceptors converging on the same target, or an uncovered track), the interceptor runs a claim-and-confirm consensus: it broadcasts a `Claim`, waits 400 ms for competing claims, yields to the higher-ID interceptor, then broadcasts `Commit`. Falls back to greedy assignment after 2 failed rounds.

All communication is ROS2 pub/sub. Topic names are defined in `contracts/contracts/topics.py`.

---

## Quick start

```bash
# Install workspace (Python 3.12, uv required)
uv sync

# Build Docker images (ROS2 Jazzy + Gazebo Harmonic)
docker compose build

# Run the full stack
docker compose up
```

To run Situation A, edit `config/scenario_default.yaml` and set `situation: A`.

---

## Development

```bash
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy .              # type check
uv run pytest              # all tests
uv run pytest path/to/test.py   # single test file
```

Each team can develop independently using mock publishers — see `docs/workstreams.md`.

---

## Configuration

All scenario parameters live in `config/scenario_default.yaml` — no code changes needed to reconfigure. The schema is validated by Pydantic on load (`contracts/contracts/config.py`).

```yaml
scenario:
  seed: 42
  situation: B          # A or B
  duration_max: 120     # seconds

shaheds:
  count: 4
  speed_mps: [15, 25]

interceptors:
  count: 3
  speed_mps: 40
  range_m: 700

comms:
  packet_loss_prob: 0.10
  consensus_window_ms: 400
```

---

## Docs

- [`docs/specification.md`](docs/specification.md) — full functional and non-functional requirements
- [`docs/architecture.md`](docs/architecture.md) — component map, topic table, algorithm details, module breakdown
- [`docs/workstreams.md`](docs/workstreams.md) — team ownership, milestones, mocking strategy, integration timeline
