# Battlefield-Readiness Spec — Gaps & Solutions

> **Purpose.** The current system is a faithful *skeleton* of a real air-defense
> decision pipeline (detect → track → score → assign → guide) and correctly
> demonstrates the Situation-A-vs-B coordination thesis. It is **not fieldable**:
> it assumes away the genuinely hard parts. This spec enumerates each gap and
> pairs it with a concrete technological solution, the reason it is the right
> tool, and a rough effort.
>
> **Scope.** Defensive counter-UAS / short-range air defense. Engagement of
> *identified hostile* targets only, always subject to rules of engagement (ROE)
> and human judgement — see the cross-cutting principle at the end.
>
> **Baseline today.** Kalman/Stone-Soup tracker (G1/G2), 1/eta threat scoring
> (G3), Hungarian 1:1 assignment over a vacuum lead-pursuit cost (G4), ZMQ/JSON
> transport, AEAD-authenticated peer messages, a mock world + clean-noise radar.
> Validated against our own simulator. 84 tests.

---

## P1 — No target identification / classification / ROE  ·  **blocking**

**Problem.** We label every track "Shahed" and auto-assign an interceptor. There
is no friend/foe/neutral discrimination, no decoy rejection, no engagement
authority. You cannot field a system that engages every radar blip.

**Solution.** A layered **classification + ID stack** feeding an **engagement-
authority gate**:
- **Micro-Doppler radar signature classification** — rotor blades produce a
  distinctive micro-Doppler spectrum; classify drone vs bird vs clutter on the
  *existing* radar return.
- **RF/ESM sensing** — most commercial/used UAS emit control/telemetry RF and
  often broadcast **Remote ID**; classify and geolocate from emissions.
- **EO/IR + CNN visual ID** — cue a camera to the track for a visual confirm.
- **Cooperative IFF / ADS-B ingest** — positively exclude known friendlies.
- **ROE rules engine + human-in-the-loop (HITL)** confirm for low-confidence or
  high-collateral engagements.

**Why it is good.** Micro-Doppler is cheap to add to a radar you already have and
is a strong, well-established discriminator. The modalities are *independent*, so
a spoof must beat all of them at once (defense-in-depth). HITL + ROE gating is
what makes the system legally and ethically deployable (the principle of
distinction). **Effort: large; do first — it is the gate to everything else.**

---

## P2 — Idealized sensing (we assume away detection)  ·  **critical**

**Problem.** We feed the tracker a clean Gaussian-noised measurement of known
truth. Reality is clutter, multipath, false alarms, terrain masking, jamming,
and the **slow-small-low** problem (tiny-RCS drones are hard to detect at all).
GNN association also collapses under the dense clutter of a saturation/swarm
attack — the actual Shahed threat model.

**Solution.**
- **Heterogeneous multi-sensor fusion** — staring Doppler radar + passive RF +
  EO/IR + acoustic, so each covers another's blind spot.
- **Networked multistatic radar** — spatial diversity defeats RCS-shaping (low-
  observable) targets that a monostatic radar misses.
- **Track-before-detect (TBD)** — integrate across frames to pull targets out
  below the single-scan SNR threshold.
- **CFAR + clutter maps** for false-alarm control; **frequency-agile / cognitive
  radar + adaptive nulling** for anti-jam.
- **Upgrade the data associator**: GNN → **JPDA** (dense clutter) or
  **GM-PHD / RFS filters** (unknown, time-varying target counts) — already on
  Stone Soup's escalation path noted in `KALMAN_TRACKER_PLAN.md`.

**Why it is good.** Modality diversity is the standard counter to single-sensor
weaknesses (radar struggles low/slow; RF misses non-emitting autonomous drones;
EO/IR needs LOS/weather). Multistatic geometry is *the* recognized answer to low
RCS. RFS/PHD filters are purpose-built for the saturation regime where target
count is unknown and changing — exactly where our GNN degrades.
**Effort: large; phase in JPDA/PHD first (software-only, big robustness win).**

---

## P3 — Optimizer ignores real interceptor physics  ·  **high**

**Problem.** The intercept model is straight-line constant-velocity lead pursuit
in a vacuum. It ignores acceleration/turn-rate limits (`max_turn_rate_deg_s` is
in the config but the optimizer never reads it), boost/burnout, drag, gravity,
the min/max engagement envelope, no-fire zones, and fratricide. The config even
has interceptors *slower than the threats*.

**Solution.** Replace the vacuum solve with a **flyout / Launch-Acceptability-
Region (LAR) model**:
- Offline, build **LAR / engagement-envelope tables** (or a reduced-order 3-DOF
  flyout sim) over boost/sustain, drag, gravity, turn-rate, and time-of-flight.
- At runtime, feasibility = "is the predicted intercept point inside the LAR,"
  and `intercept_time` comes from the flyout, not `distance/speed`.
- Add a **Pk-vs-geometry** term and keep guidance honest with augmented PN.

**Why it is good.** LAR tables are exactly how real fire-control evaluates whether
a shot is takeable: expensive physics computed *offline*, an O(1) lookup at
runtime, so the optimizer's cost matrix stays cheap while becoming physically
grounded. It directly fixes the binary range check that today's optimizer relies
on. **Effort: medium; high credibility-per-line.**

---

## P4 — Not real-time / not hardened  ·  **high**

**Problem.** A closed fire-control loop on Python + JSON-over-ZMQ at a few Hz,
with `datetime.now()` wall-clocks and no cross-node time sync. GC pauses, best-
effort TCP, and no latency guarantees disqualify it as an engagement loop.

**Solution.**
- Move the **real-time core off Python** — a C++/Rust engagement core on
  **PREEMPT_RT Linux / an RTOS**, with the planning + UI tiers staying in Python.
- **DDS (ROS 2 / Cyclone DDS) with real-time QoS** for the bus; **binary
  serialization (CDR/FlatBuffers)** instead of JSON.
- **PTP (IEEE 1588) or GPS-disciplined clocks** for the time sync that
  multi-sensor track fusion requires.

**Why it is good.** DDS is the actual middleware of real robotics/defense systems
(and what ROS 2 runs on). Crucially, **our `Bus` protocol already abstracts the
transport** — so this is a localized swap behind the same interface, not a
rewrite; the design anticipated it. RT QoS gives bounded latency; PTP solves the
time-alignment that wall-clocks cannot. **Effort: large (the RT core); the DDS
swap itself is medium.**

---

## P5 — Deterministic single-shot engagement model  ·  **medium**

**Problem.** Equal lethality, binary feasible/infeasible, strict 1:1 assignment,
no kill probability, no shoot-shoot or salvo. Hungarian cannot express "two
interceptors on the most dangerous threat."

**Solution.** Reformulate as **Weapon-Target Assignment (WTA)** — maximize
expected protected value with per-pairing kill probabilities and **multiple
interceptors per high-value target**:
- Small N: solve the static WTA as a **MILP** (optimal, within the 2 s budget).
- Saturation: a **metaheuristic** (greedy + local search, auction, or
  Lagrangian relaxation) for scale.
- Keep Hungarian as the linear special case / MILP warm-start.

**Why it is good.** WTA is the doctrine-correct model for exactly this problem;
it natively expresses salvo/shoot-shoot and Pk, which Hungarian structurally
cannot. It layers on the existing cost machinery, and MILP gives provable
optimality at the scale a hackathon demo runs. **Effort: medium.**

---

## P6 — Validated only against our own simulator  ·  **medium**

**Problem.** "84 tests pass" means the code matches our *toy model*. The world and
the filter share a constant-velocity assumption, so we never exercise the
maneuvering/decoy/swarm/clutter regimes that break real trackers.

**Solution.** A **validation hierarchy**:
- **Monte-Carlo campaigns** with injected model mismatch (maneuvers, decoys,
  clutter density, jamming) and quantitative metrics: track purity, false-track
  rate, leakage / Pk, latency percentiles.
- **Replay against high-fidelity sims / recorded data** (e.g. AFSIM-class 6-DOF,
  recorded radar plots).
- **Hardware-in-the-loop (HIL)** with a real radar feed.

**Why it is good.** It separates "matches our model" from "works against reality"
and surfaces the regimes our self-consistent sim hides. Monte-Carlo + injected
mismatch is the cheap first step; AFSIM/HIL is the recognized DoD V&V path; the
metrics make the A-vs-B claim defensible rather than anecdotal.
**Effort: medium; start with Monte-Carlo (software-only).**

---

## P7 — Comms robustness under EW  ·  **medium**

**Problem.** Situation B's edge *is* the peer mesh — which is also its single
point of failure. We authenticate messages (AEAD seal/unseal) but have no
replay protection / key management, and nothing at the RF layer.

**Solution.** Harden the mesh: **anti-jam waveforms** (frequency-hopping spread
spectrum / MANET radios), **partition-tolerant mesh routing** (we already model
partitions in `MockBus`), and complete the crypto with **nonce/replay protection
and key rotation/management** on top of the existing AEAD.

**Why it is good.** It protects the exact mechanism that gives Situation B its
advantage; the partition modeling and AEAD are already in place, so this extends
proven scaffolding rather than starting over. **Effort: medium.**

---

## Prioritized roadmap

| # | Gap | Priority | First, cheapest step |
|---|-----|----------|----------------------|
| P1 | Target ID + ROE gate | **Blocking** | Micro-Doppler classifier + HITL gate |
| P2 | Real sensing | Critical | GNN → JPDA/GM-PHD (software-only) |
| P3 | Interceptor physics | High | LAR feasibility (use `max_turn_rate`) |
| P4 | Real-time hardening | High | DDS swap behind the existing `Bus` |
| P5 | WTA / shoot-shoot | Medium | MILP WTA over the current cost matrix |
| P6 | Validation vs reality | Medium | Monte-Carlo with injected mismatch |
| P7 | Comms / EW | Medium | Replay protection + key rotation |

## Cross-cutting principle — human judgement & ROE

Every solution above is a **decision aid**, not an autonomous kill chain. The
system identifies, prioritizes, and recommends; a human with engagement authority
decides, under ROE and the law-of-armed-conflict principle of distinction. P1's
authority gate is therefore not an optional feature — it is the architectural
backbone the rest hangs from.
