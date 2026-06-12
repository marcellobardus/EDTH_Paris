# Architecture Design
## Real-Time Multi-Interceptor Coordination

---

## 1. Component Map

Ground station is active pre-launch only. Post-launch, interceptors are fully autonomous and coordinate peer-to-peer.

```mermaid
graph TD
    subgraph SIM["PC 1 — Simulation"]
        PB[PyBullet World]
        ED[Shahed Agents]
        SM[Radar Sensor Model]
        ENG[Engagement Detector]
    end

    subgraph PC2["PC 2 — Ground Station pre-launch + Visualization"]
        subgraph GS["Ground Station (active pre-launch only)"]
            TF[Track Fusion]
            TA[Threat Assessor]
            AO[Assignment Optimizer]
            TF --> TA --> AO
        end
        subgraph VIZ["Visualization"]
            V3D[3D View]
            DB[Dashboard]
            CSV[Metrics Logger]
        end
    end

    subgraph AGENTS["Interceptor Agent Processes (PC 2)"]
        I1["Interceptor 1\nguidance · comms · re-tasking"]
        I2["Interceptor 2\nguidance · comms · re-tasking"]
        IN["..."]
    end

    SM -->|detections| TF
    AO -->|assignments at launch| AGENTS
    I1 <-->|"peer-to-peer (Sit. B)"| I2
    I2 <-->|"peer-to-peer (Sit. B)"| IN
    AGENTS -->|waypoints| PB
    PB -->|ground truth| V3D
    AGENTS -->|state| DB
    ENG -->|events| CSV
```

---

## 2. Communication Topology

```mermaid
graph LR
    subgraph PreLaunch["Pre-launch (GS active)"]
        SM[Radar Sensor] -->|/radar/detections| TF[Track Fusion]
        TF -->|/gs/tracks| TA[Threat Assessor]
        TA -->|/gs/threats| AO[Optimizer]
        AO -->|/gs/assignments| AGENTS[Interceptors]
    end

    subgraph InFlight["In-flight (Sit. B — peer-to-peer)"]
        I1[Interceptor 1] -->|"/interceptors/1/state\n/interceptors/1/claim\n/interceptors/1/commit"| BUS((pub/sub bus))
        I2[Interceptor 2] -->|"/interceptors/2/state\n..."| BUS
        BUS --> I1
        BUS --> I2
    end
```

| Topic | Publisher | Subscribers | Phase |
|---|---|---|---|
| `/radar/detections` | Radar Sensor | Track Fusion | Pre-launch |
| `/gs/tracks` | Track Fusion | Threat Assessor, Dashboard | Pre-launch |
| `/gs/threats` | Threat Assessor | Optimizer | Pre-launch |
| `/gs/assignments` | Optimizer | Interceptors | At launch |
| `/interceptors/{id}/state` | Interceptor | All peers, Dashboard | In-flight (Sit. B) |
| `/interceptors/{id}/claim` | Interceptor | All peers | In-flight (Sit. B) |
| `/interceptors/{id}/commit` | Interceptor | All peers | In-flight (Sit. B) |

---

## 3. Situation A vs B — Operational Sequence

```mermaid
sequenceDiagram
    participant GS as Ground Station
    participant I1 as Interceptor 1
    participant I2 as Interceptor 2
    participant S1 as Shahed 1
    participant S2 as Shahed 2

    Note over GS: Track fusion + Hungarian optimizer
    GS->>I1: Assignment: target S1
    GS->>I2: Assignment: target S2
    Note over GS: GS role ends here

    Note over I1,I2: Launch — interceptors are autonomous

    I1-->>S1: Intercept S1 ✓

    rect rgb(255,220,220)
        Note over I1,I2: Situation A
        Note over I1: I1 expended, silent
        Note over I2: I2 has no info — continues to S2
        Note over I2: If S2 already dead or out of range → miss ✗
    end

    rect rgb(220,255,220)
        Note over I1,I2: Situation B
        I1->>I2: COMMIT(I1, S1 killed) via state broadcast
        Note over I2: Onboard: detects I1 free, S3 uncovered
        I2->>I1: CLAIM(I2, S3)
        Note over I1: No conflict — I1 also free, picks S4
        I1->>I2: COMMIT(I1, S4)
        I1-->>S4: Intercept ✓
        I2-->>S3: Intercept ✓
    end
```

---

## 4. Onboard Re-tasking Protocol (Situation B)

```mermaid
flowchart TD
    A[State update received from peer] --> B[Rebuild local awareness picture]
    B --> C{Coverage conflict?}
    C -- No --> Z[Continue guidance to current target]
    C -- Yes --> D[Select best uncovered track T]
    D --> E[Broadcast CLAIM-self_id-T]
    E --> F[Wait 400 ms]
    F --> G{Higher-ID claim\nreceived for T?}
    G -- No --> H[Broadcast COMMIT-self_id-T\nUpdate own assignment]
    G -- Yes --> I{Round 2?}
    I -- No --> D2[Pick next best uncovered track]
    D2 --> E
    I -- Yes --> J[Fallback: greedy — pick closest\nuncovered track and commit]
    H --> Z
    J --> Z
```

**Coverage conflict** is defined as: any active track has 0 interceptors assigned in the local picture, AND at least one interceptor (including self) is assigned to a track with 2+ interceptors, OR to a track that is already dead.

---

## 5. Module Breakdown

### `sim/` — Simulation Engine (PC 1)
```
sim/
├── world.py              # PyBullet init, physics step loop
├── shahed_agent.py       # Shahed: fly toward target at configurable speed
├── interceptor_body.py   # PyBullet body: receives guidance waypoints, applies forces
├── radar_sensor.py       # Gaussian noise model, FOV/range filter
├── engagement.py         # Proximity check → removes both bodies, emits event
└── config_loader.py      # Loads and validates YAML config
```

### `gs/` — Ground Station (PC 2, pre-launch only)
```
gs/
├── track_fusion.py           # Kalman filter bank, one filter per track
├── track_manager.py          # Track birth/death (gating, coasting)
├── threat_assessor.py        # score = w1/distance + w2*speed + w3/ETA
└── assignment_optimizer.py   # scipy.optimize.linear_sum_assignment
```

### `agent/` — Interceptor (PC 2, one process per interceptor)
```
agent/
├── interceptor_agent.py   # Main loop: guidance + comms + re-tasking
├── guidance.py            # Proportional navigation, 100 ms update
├── awareness.py           # Local picture: tracks → {track_id: interceptor_id}
├── retasking.py           # Claim-and-confirm protocol, fallback greedy
└── comms.py               # Pub/sub + packet drop simulation
```

### `viz/` — Visualization (PC 2)
```
viz/
├── pybullet_viz.py   # 3D overlays: assignment lines, radar circles, labels
├── dashboard.py      # Metrics window
└── metrics_logger.py # CSV: per-run stats
```

### `config/`
```
config/
├── scenario_default.yaml
└── schema.py              # Pydantic validation
```

---

## 6. Scenario Config Schema

```yaml
scenario:
  seed: 42
  target_position: [500, 500, 0]
  duration_max: 120                  # seconds
  situation: B                       # A or B

radars:
  - position: [100, 100, 10]
    range: 800
    fov_deg: 360
    noise_std: 5
  - position: [400, 200, 10]
    range: 600
    fov_deg: 360
    noise_std: 8

shaheds:
  count: 4
  speed_mps: [15, 25]
  spawn_radius: 1000
  spawn_angle_spread_deg: 360

interceptors:
  count: 3
  speed_mps: 40
  max_turn_rate_deg_s: 30
  range_m: 700
  launch_position: [480, 480, 0]

comms:
  publish_rate_hz: 5
  packet_loss_prob: 0.10
  consensus_window_ms: 400
  max_claim_rounds: 2
```

---

## 7. Key Algorithms

### Assignment Optimizer (Hungarian, pre-launch)

```
C[i][j] = intercept_time[i][j] / threat_score[j]   if distance[i][j] < range[i]
         = 1e9                                        otherwise

scipy.optimize.linear_sum_assignment(C)  →  O(n³), < 1 ms for n ≤ 10
```

### Proportional Navigation (onboard guidance)

```python
# Every 100 ms
R     = target_pos - self_pos
R_dot = target_vel - self_vel
omega = cross(R, R_dot) / dot(R, R)   # LOS angular rate
a_cmd = N * self_speed * omega         # N ≈ 3–5
```

Robust to track noise and packet loss — works on LOS rate, not exact position.

### Claim-and-Confirm (onboard re-tasking, Situation B)

```python
def retask(self):
    T = self.awareness.best_uncovered_track()
    for round in range(MAX_ROUNDS):
        self.broadcast(Claim(self.id, T))
        competing = self.wait_for_claims(T, timeout=CONSENSUS_WINDOW)
        if not any(c.interceptor_id > self.id for c in competing):
            self.broadcast(Commit(self.id, T))
            self.assignment = T
            return
        T = self.awareness.next_best_uncovered(exclude=[T])
    # fallback
    self.assignment = self.awareness.closest_uncovered()
```

---

## 8. Development Milestones

| # | Deliverable |
|---|---|
| M1 | YAML config + PyBullet world with Shaheds flying toward target |
| M2 | Radar sensor model + Kalman track fusion |
| M3 | Threat assessor + Hungarian optimizer + interceptor launch → **Situation A end-to-end** |
| M4 | PN guidance + engagement detection (verify hit rate) |
| M5 | Peer comms + claim-and-confirm re-tasking → **Situation B end-to-end** |
| M6 | Dashboard + CSV metrics logger (A vs B comparison) |
| M7 | PyBullet 3D overlays + demo polish |
