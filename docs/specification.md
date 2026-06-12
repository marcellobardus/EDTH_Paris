# System Requirements Specification
## Real-Time Multi-Interceptor Coordination — EDTH Paris Hackathon

---

## 1. Purpose & Scope

This system demonstrates that interceptors capable of mid-flight communication achieve a measurably higher threat-neutralization rate than interceptors flying pre-assigned missions without coordination.

**In scope:** simulation, sensor fusion, threat assessment, assignment optimization, guidance, mid-flight re-tasking, visualization.  
**Out of scope:** decoy discrimination, electronic warfare, terrain occlusion.

---

## 2. Definitions

| Term | Meaning |
|---|---|
| Ground Station (GS) | Central node that fuses sensor data and computes initial assignments before launch |
| Interceptor | A friendly missile/drone with one engagement (one shot) |
| Shahed | An enemy drone flying toward the protected target |
| Track | A fused, filtered estimate of a Shahed's state (position, velocity) |
| Assignment | A (interceptor, track) pair computed by the optimizer |
| Situation A | Baseline: fixed pre-launch assignment, no mid-flight communication |
| Situation B | Proposed: interceptors share state mid-flight and can reassign themselves |
| Engagement | Interceptor reaches proximity threshold of target → both removed |
| Convergence failure | Two interceptors converge on the same Shahed while a second Shahed goes uncovered |

---

## 3. System Overview

```mermaid
graph TD
    subgraph SIM["Simulation — PC 1"]
        PB[PyBullet World]
        ED[Shahed Agents]
        SM[Radar Sensor Model]
    end

    subgraph GS["Ground Station — PC 2"]
        TF[Track Fusion]
        TA[Threat Assessor]
        AO[Assignment Optimizer]
        RE["Re-tasking Engine (Sit. B only)"]
        TF --> TA --> AO --> RE
    end

    subgraph AGENTS["Interceptor Agents (PC 2 or Pi)"]
        I1[Agent 1]
        I2[Agent 2]
        IN[...]
    end

    subgraph VIZ["Visualization"]
        V3D[3D View — PyBullet GUI]
        DB[Dashboard — metrics]
    end

    SM -->|detections| TF
    AO -->|initial assignments| AGENTS
    RE -->|reassignments| AGENTS
    AGENTS -->|state + events| RE
    AGENTS -->|waypoints| PB
    PB -->|ground truth| V3D
    GS -->|tracks + assignments| DB
```

---

## 4. Failure Modes Addressed by Communication

Situation B must detect and correct two distinct failure modes that Situation A cannot handle:

**Failure mode 1 — Dead target redundancy:** Interceptor I1 neutralizes Shahed S1. In Situation A, I2 (assigned S1 as backup or due to overlap) continues toward the already-dead S1, wasting a shot while S2 gets through.

**Failure mode 2 — Convergence on proximate targets:** Two Shaheds fly close together. Due to track noise or PN guidance drift, two interceptors both lock onto the same Shahed S2, leaving S1 uncovered. In Situation A this is undetectable; in Situation B the re-tasking engine sees two interceptors assigned to S2 and reassigns one.

```mermaid
sequenceDiagram
    participant GS as Ground Station
    participant I1 as Interceptor 1
    participant I2 as Interceptor 2
    participant S1 as Shahed 1
    participant S2 as Shahed 2

    GS->>I1: Assign → S1
    GS->>I2: Assign → S2
    Note over S1,S2: Shaheds flying close together

    rect rgb(255,220,220)
        Note over I1,I2,S1,S2: Situation A — convergence failure
        Note over I1: Track noise locks I1 onto S2
        I1-->>S2: hits S2 (redundant)
        I2-->>S2: hits S2 (wasted)
        Note over S1: S1 reaches target ✗
    end

    rect rgb(220,255,220)
        Note over I1,I2,S1,S2: Situation B — detected and corrected
        I1->>GS: state broadcast (targeting S2)
        I2->>GS: state broadcast (targeting S2)
        Note over GS: Two agents on S2, S1 uncovered → reassign
        GS->>I1: Re-assign → S1
        I1-->>S1: Intercept S1 ✓
        I2-->>S2: Intercept S2 ✓
    end
```

---

## 5. Functional Requirements

### FR-1: Simulation Engine
- FR-1.1 Simulate Shaheds flying toward a fixed target with physically realistic trajectories (PyBullet rigid body dynamics).
- FR-1.2 Simulate interceptors following waypoints with realistic kinematics (speed, turn-rate limits).
- FR-1.3 Engagement detection: when an interceptor reaches within a configurable proximity threshold of its assigned target, both are removed.
- FR-1.4 All scenario parameters are defined in a YAML config file (no code changes required to vary the scenario).

### FR-2: Sensor Model
- FR-2.1 Radar positions, range, and field-of-view are set in the YAML config.
- FR-2.2 Each radar adds Gaussian noise to position measurements and respects range/FOV limits.
- FR-2.3 Radar detections are published at a configurable rate (default: 10 Hz).

### FR-3: Track Fusion
- FR-3.1 The GS fuses detections from all radars into one track per Shahed using a Kalman filter (constant-velocity model).
- FR-3.2 Track fusion updates the unified operational picture at ≥ 5 Hz.

### FR-4: Threat Assessment
- FR-4.1 Each track is scored by: distance to target, estimated time-to-impact, and speed.
- FR-4.2 Scores are recomputed every update cycle and drive assignment priority.

### FR-5: Assignment Optimizer (Pre-Launch)
- FR-5.1 The GS computes an optimal assignment using the Hungarian algorithm, minimizing intercept time weighted by threat score.
- FR-5.2 Feasibility is enforced: an interceptor cannot be assigned a target outside its range envelope.
- FR-5.3 The full assignment is issued within **2 seconds** of the go signal.
- FR-5.4 Unmatched interceptors hold position; uncovered threats are flagged.

### FR-6: Interceptor Guidance
- FR-6.1 Each interceptor follows proportional navigation (PN) toward the predicted intercept point of its assigned track.
- FR-6.2 Guidance recomputes every 100 ms using the latest available track.
- FR-6.3 Configurable maneuverability limits (max turn rate, max speed) are enforced.

### FR-7: Mid-Flight Communication and Re-tasking (Situation B)
- FR-7.1 Each interceptor broadcasts its state (position, velocity, assigned track ID, alive) at 5 Hz.
- FR-7.2 Packet loss is simulated: each message is dropped with configurable probability (default: 10%).
- FR-7.3 When an interceptor neutralizes its target, it broadcasts a `target_killed` event and requests a new assignment.
- FR-7.4 The re-tasking engine recomputes assignments for free interceptors within **2 seconds** of any `target_killed` event.
- FR-7.5 **Convergence detection:** the re-tasking engine continuously checks that each active track has exactly one interceptor assigned. If a track has two interceptors assigned and another track has none, it reassigns one interceptor to the uncovered track.
- FR-7.6 An interceptor that receives a new assignment immediately re-routes without returning to base.

### FR-8: Visualization
- FR-8.1 PyBullet GUI shows in real time: interceptors (blue), Shaheds (red), radar coverage circles, target, engagement events.
- FR-8.2 A dashboard shows: threats remaining, interceptors active, ammo consumed, current assignment map, elapsed time.
- FR-8.3 Per-run metrics are written to CSV: scenario config, situation (A/B), threats neutralized, threats that reached target.

---

## 6. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-1 | Assignment decision issued in < 2 s (pre-launch and re-tasking) |
| NFR-2 | Interceptor guidance loop runs at ≥ 10 Hz |
| NFR-3 | Track fusion end-to-end latency < 200 ms |
| NFR-4 | Trajectories are physics-based — no teleportation, no instant turns |
| NFR-5 | System remains functional at up to 30% packet loss |
| NFR-6 | All scenario parameters configurable via YAML without code changes |
| NFR-7 | Python-only codebase; ROS2 or ZeroMQ for inter-process communication |

---

## 7. Constraints

- Each interceptor carries one missile (one engagement, no reload).
- Once launched, an interceptor cannot return to base.
- Interceptors have a configurable max range; targets outside range cannot be assigned.
- The protected target is a single fixed point.
- No decoy discrimination: all tracked objects are treated as real threats.

---

## 8. Evaluation Criteria

Same random seed, same YAML config, Situation A vs Situation B:

| Metric | Situation A | Situation B |
|---|---|---|
| Threats neutralized / total | baseline | must be ≥ baseline |
| Convergence failures (two shots on one target) | expected > 0 | must be lower |
| Interceptors wasted on dead targets | expected > 0 | must be lower |
| Threats reaching the target | baseline | must be lower |

A statistically meaningful improvement across all metrics constitutes a successful demonstration.
