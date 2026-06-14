# System Architecture

Real-Time Multi-Interceptor Coordination — EDTH Paris.

The system compares two scenarios: **Situation A** (interceptors fly fixed pre-launch
assignments, no comms) vs **Situation B** (interceptors share state mid-flight and
re-assign via claim-and-confirm consensus). All inter-process communication is ROS2
pub/sub; topic names come from `contracts/contracts/topics.py`.

## Component & Data-Flow Overview

```mermaid
flowchart TB
    subgraph contracts["contracts/ — Team 4 (shared foundation)"]
        C[messages.py · topics.py · config.py]
    end

    subgraph sim["sim/ — Team 1"]
        World["sim.world<br/>Gazebo headless (gz sim -s -r)<br/>+ gz-launch websocket :9002"]
        Driver["sim.driver<br/>authority: Shahed kinematics +<br/>GzBridge cmd_vel / pose loop"]
    end

    subgraph gs["gs/ — Team 2 (Ground Station)"]
        Fusion["Track fusion (Kalman)"]
        Threat["Threat scoring"]
        Assign["Hungarian assignment<br/>scipy.linear_sum_assignment"]
    end

    subgraph agents["agent/ — Team 3 (per interceptor, INTERCEPTOR_ID)"]
        PN["PN guidance (100 ms)"]
        Peer["Peer comms + claim-and-confirm"]
    end

    subgraph viz["viz/ — Team 4"]
        Overlay["Gazebo overlays · metrics dashboard · CSV logger"]
        GzWeb["gzweb :8080"]
    end

    Driver -->|"/radar/detections"| Fusion
    Fusion -->|"/gs/tracks"| Threat
    Threat -->|"/gs/threats"| Assign
    Assign -->|"/gs/assignments (at launch)"| Peer

    Peer -->|"/interceptors/{id}/waypoint (10 Hz)"| Driver
    Driver -->|"true pose via /world/.../pose/info"| PN
    PN --> Peer

    Driver -->|"/simulation/ground_truth"| Overlay
    Driver -->|"/simulation/engagement"| Overlay
    World -.->|websocket| GzWeb

    C -.imports.-> sim
    C -.imports.-> gs
    C -.imports.-> agents
    C -.imports.-> viz

    classDef shared fill:#fef3c7,stroke:#d97706
    class contracts shared
```

## Pre-launch Flow (Situation A & B — Ground Station active)

```mermaid
sequenceDiagram
    participant Sim as sim.driver
    participant GS as gs (Ground Station)
    participant Int as Interceptors

    Sim->>GS: /radar/detections
    Note over GS: Kalman fusion → tracks
    GS->>GS: /gs/tracks → /gs/threats
    Note over GS: Hungarian optimizer<br/>C[i][j] = intercept_time / threat_score
    GS->>Int: /gs/assignments (one-shot at launch)
    Note over Int: GS role ends at launch
```

## In-flight Re-tasking (Situation B only — peer-to-peer consensus)

```mermaid
sequenceDiagram
    participant I1 as Interceptor i1
    participant I2 as Interceptor i2
    participant Sim as sim.driver

    loop 5 Hz broadcast
        I1->>I2: /interceptors/i1/state
        I2->>I1: /interceptors/i2/state
    end

    Note over I1,I2: Re-tasking triggered
    I1->>I2: /interceptors/i1/claim
    I2->>I1: /interceptors/i2/claim
    Note over I1,I2: wait 400 ms · yield to higher interceptor_id
    I1->>I2: /interceptors/i1/commit
    Note over I1,I2: Fallback: greedy (closest uncovered)<br/>after 2 failed rounds / packet loss

    loop 10 Hz PN pursuit
        I1->>Sim: /interceptors/i1/waypoint
    end
```

## Deployment (Docker Compose)

```mermaid
flowchart LR
    Base["base service<br/>ROS2 Jazzy + Gazebo Harmonic + uv<br/>(built first)"]
    Base --> SimC[sim]
    Base --> GsC[gs]
    Base --> AgentC["agent ×N<br/>(INTERCEPTOR_ID per container)"]
    Base --> VizC[viz]

    SimC -.->|"DDS: network_mode: host + ipc: host"| GsC
    GsC -.->|DDS| AgentC
    AgentC -.->|DDS| SimC
    SimC -.->|"/simulation/*"| VizC

    Note["⚠ Every DDS service needs ipc: host —<br/>without it Fast DDS silently drops<br/>every cross-container sample.<br/>gzweb is exempt (websocket)."]

    classDef warn fill:#fee2e2,stroke:#dc2626
    class Note warn
```

> **ID bridge:** agent id `i{n}` ↔ gz model `interceptor_{n}`; track id `t{n}` ↔ gz model `shahed_{n}`.
