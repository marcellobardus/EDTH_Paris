# PHALANX — Distributed Asynchronous Interceptor–Target Assignment

**Project spec — European Defense Tech Hackathon, Paris, June 12–14 2026**
**Challenge fit:** **Alta Ares #2 — Real-Time Multi-Interceptor Coordination and Threat Assignment** (directly: *"Coordination algorithms: share targeting data across the interceptor network… Dynamic re-tasking… consensus"*). Cross-links to **#8 / LEUKO** (works under contested, intermittent comms).
**Name:** *Phalanx* — a battle formation that fights as one coordinated body with **no single commander dictating each soldier's move**; every unit adjusts to its neighbors. (Also the name of a real CIWS air-defense system — fitting double meaning.)

**Concept:** Multiple interceptor drones must agree, **among themselves**, on who engages which target — and keep re-agreeing as targets appear, maneuver, and interceptors launch or are lost — **without a central coordinator**, tolerant to messages arriving late, out of order, or not at all. Each interceptor bids on targets by local cost (time-to-intercept / kill probability); a **consensus-based auction** resolves conflicts into a conflict-free assignment that converges even when the agents' views are out of sync.

---

## 1. The scenario (what we're modelling)

One launch station, interceptor drones **A, B, C, D** (launchable from *different* locations, so each has a different cost to each target), versus enemy targets **1, 2, 3** entering over time. The assignment is **dynamic**: it changes every time the world changes.

- **t0** — Only target **1** is visible. Launch **A**. Trivially: `A → 1`.
- **t1** — Target **2** enters; launch **B**. But A is geometrically closer to 2 and B to 1, so the optimal assignment *swaps*: `A → 2`, `B → 1`.
- **t2** — Target **2 maneuvers** (trajectory change). Now A is closer to **1** again, so A **retargets**: `A → 1`, `B → 2`.
- **t3** — Target **3** enters; launch **C** (with **D** held in reserve). Settles to `A → 1`, `B → 2`, `C → 3`, `D` reserve.

The hard part is not computing one assignment — it's that the interceptors must **reach and maintain this agreement themselves**, asynchronously, while the EW environment makes their links to each other (and to the station) intermittent.

## 2. System model & assumptions

- **Agents:** the interceptors (A–D). Each has onboard compute (Raspberry-Pi-class), local sensing / a shared track feed, and knows its own kinematics.
- **Tasks:** the targets (1–3), appearing/disappearing/maneuvering over time.
- **Interceptor state:** each interceptor *i* carries `(assigned target jᵢ, hit-confidence pᵢⱼ, ETA — estimated time-to-intercept, status)` with `status ∈ {reserve, committed, engaged, expended}` and **`pᵢⱼ` = probability it kills *j*** given current geometry (from **TALOS**). State is *what* it targets **and** *how confident it is* — not just the target id.
- **Score / bid:** because confidence is in the state, the bid is **marginal expected value**, not raw proximity (see §3). TTI enters as a feasibility gate (`p = 0` if *i* can't reach *j* before *j*'s deadline) and a tiebreaker.
- **Comms:** peer-to-peer / mesh, **asynchronous** — messages can be delayed, reordered, duplicated, or dropped; the graph can partition and re-merge. Assume only *eventual* connectivity.
- **No central authority** in the decision loop. The station may launch drones and inject targets, but it does **not** dictate assignments (it may be jammed or destroyed).
- **Constraints & attrition:** each interceptor engages **≤ 1** target at a time; live targets need **≥ 1** interceptor (more if confidence is low); spares stay in reserve. Interceptors are **consumable** — a kamikaze/FPV interceptor that engages is **expended** and leaves the pool; targets leave when killed. The agent set *and* task set both **shrink and grow** over time.

## 3. The optimization problem (formally)

With **confidence** in play the objective is not "minimize total distance" but **maximize expected destroyed target value** — the classic **Weapon–Target Assignment (WTA)** objective:

> **maximize**  `Σ_j V_j · [ 1 − Π_i (1 − p_ij · X[i][j]) ]`
> **subject to**  `Σ_j X[i][j] ≤ 1` (one target per interceptor), each live target covered, feasibility (`p_ij = 0` if *i* can't intercept *j* before *j*'s deadline), and interceptor/ammo availability.

where `V_j` = value/threat of target *j*, `p_ij` = *i*'s kill probability on *j*. The `1 − Π(1 − p)` term is the **combined kill probability** of all interceptors on *j* — which is *why* a high-value, low-confidence target should draw **multiple** interceptors (layered defense), and why piling shots on an already-high-`p` kill wastes them (diminishing returns / submodularity).

**Local vs global — and the bridge.** Locally each interceptor wants its own best (highest-`p`, soonest) target — greedy. Globally the system wants max total expected destroyed value, which may force an interceptor onto its *second* choice so another threat is covered (exactly the t1 swap). The reconciler is the **auction's market prices**: the bid an agent must beat, `y[j]`, is the **dual variable (shadow price)** of the global assignment. Agents bidding against prices is *distributed primal–dual optimization* — selfish local bids + price consensus provably converge to the **global** optimum, with nobody computing it centrally.

Each interceptor's **bid** is therefore its **marginal expected value** of joining *j* given who's already on it:
`Δ_i(j) = V_j · p_ij · Π_{k already on j}(1 − p_kj)`.

Statically this is NP-hard WTA (the assignment LP is its linear relaxation); dynamically it must be solved **distributively, online, asynchronously**, re-converging whenever a `p_ij` changes (maneuver), a target appears/dies, or an interceptor launches / **is expended**.

## 3.5 What this problem is actually made of (anatomy)

Strip away the scenario and this is a **Decentralized, Dynamic Weapon–Target Assignment (Dec-DWTA) problem under uncertainty** — a superposition of five classical problems, which is why it's hard and why no single textbook algorithm covers it:

1. **Assignment / matching** *(combinatorial optimization)* — bipartite interceptor↔target matching; Hungarian / auction at the core. With confidence it generalizes to **WTA** (max expected destroyed value): NP-hard.
2. **Scheduling under deadlines** *(operations research)* — each target has a deadline (time-to-impact); each interceptor a feasibility window, a flyout "processing time," and is **single-use**. = parallel-machine scheduling with deadlines, stochastic rewards, consumable machines. → *"proper scheduling."*
3. **Sequential decision under uncertainty** *(MDP / stochastic control)* — `p < 1` + irreversible commitment + information arriving over time ⇒ an MDP. The **shoot–look–shoot** loop (commit → observe battle-damage → re-decide) and reserve management live here. → *"optimal decision making over time."*
4. **Distributed consensus** *(distributed systems)* — agreeing on all the above with no central node, async and lossy — CBBA. → *"consensus."*
5. **Adversarial dynamics** *(game theory / pursuit–evasion)* — targets maneuver to defeat intercepts (the t2 jink); a min-max flavor; assignments must be robust to evasion.

**The local-vs-global axis is a primal–dual structure** (§3): local greedy bids + consensus on market prices = distributed primal–dual ascent to the global optimum; the prices *are* the coordination signal. Layers 1–2 say *who & when*; layer 3 says *commit or wait*; layer 4 makes it work *without a boss*; layer 5 keeps it honest against a thinking enemy. **PHALANX** is the layer-4 engine; **TALOS** feeds layers 1–3 the geometry & confidence; the shoot-look-shoot policy is layer 3.

## 4. The state matrix over time (t0 → t4) — the centerpiece

**One matrix.** Rows = time, columns = interceptors (A B C D). Each cell is the interceptor's full **state enum** `(target, confidence p, time-to-intercept τ, status)` — *what* it targets **and** *how confident it is*, not just an assignment id.

**Legend** — `Tn` assigned target · `p` hit-confidence (kill prob, from TALOS) · `ETA` estimated time-to-intercept (s) · status: `cmt` committed / `eng` terminal / `rsv` reserve / `spent` expended · `—` not launched.

| t | A | B | C | D |
|---|---|---|---|---|
| **t0** | `T1 · p.70 · ETA 12s · cmt` | `—` | `—` | `—` |
| **t1** | `T2 · p.80 · ETA 9s · cmt` | `T1 · p.82 · ETA 8s · cmt` | `—` | `—` |
| **t2** | `T1 · p.85 · ETA 7s · cmt` | `T2 · p.78 · ETA 10s · cmt` | `—` | `—` |
| **t3** | `T1 · p.88 · ETA 6s · cmt` | `T2 · p.80 · ETA 9s · cmt` | `T3 · p.82 · ETA 8s · cmt` | `rsv` |
| **t4** | `✗ spent (killed T1)` | `T2 · p.45 · ETA 11s · eng` | `T3 · p.84 · ETA 7s · eng` | `T2 · p.70 · ETA 9s · cmt ⟵layered` |

**What drives each transition** (the assignment is re-derived from the cells, distributively — §6):

- **t0:** only T1 seen → launch **A** → `A:T1`.
- **t0→t1:** T2 enters, launch **B**. A reaches T2 sooner than T1 (ETA 9s vs 14s) and B reaches T1 sooner (ETA 8s vs 16s) → **swap**: `A:T2, B:T1`.
- **t1→t2:** T2 jinks; A's geometry to **T1** improves (ETA 7s, p.85) while its T2 shot decays → A **retargets T1**, B takes T2 — a distributed cross-swap via the auction (trace in §6.5).
- **t2→t3:** T3 enters, launch **C** → `C:T3`; A & B hold; **D** stays in reserve (4 interceptors, 3 targets).
- **t3→t4:** A's intercept on T1 completes → **T1 killed, A spent** (attrition, both sides). T2 jinks again → B's confidence collapses to **p.45**, below the single-shot floor → **layer** the reserve **D** onto T2. Combined kill prob `1 − (1−.45)(1−.70) = .835` ✓. C continues T3.

**Shoot-look-shoot branch (t4):** had battle-damage assessment shown T1 *survived* (A missed), T1 re-enters the pool **while A is gone** — the auction re-runs with one fewer interceptor and **D** (or a fresh launch) is pulled onto T1. Confidence *after observation* drives the re-plan — the sequential, feedback-driven core (anatomy layer 3).

The cell is deliberately the *full state*: the bid in §6 is computed straight from it — `Δ_i(j) = V_j · p · (residual survival)`, with `ETA` gating feasibility. Over t0→t4 both the **agent set and the task set shrink and grow** (B & C launch, D pulled from reserve, A expended, T1 killed), and every reassignment is **agreed by the interceptors themselves**, not handed down by a station.

## 5. Why distributed, not central

A central Hungarian solver at the station is simpler — and wrong for this environment:

- **No single point of failure.** Station jammed or destroyed → the interceptors keep coordinating.
- **Lower latency to maneuvers.** The interceptor that *sees* target 2 jink re-bids immediately, instead of round-tripping through a station.
- **Contested-comms tolerance.** The whole premise of #2 (and #8) is EW degradation; a central scheme stalls when its links drop. (Ties directly to **LEUKO / #8**.)
- **Scales.** No central bottleneck as interceptor/target counts grow.

The cost of going distributed is the hard part: agents have **inconsistent, out-of-sync views**, and must still converge to a conflict-free assignment. That is exactly what the consensus algorithm provides.

## 6. The algorithm — Consensus-Based Auction (CBAA/CBBA)

Built on **CBBA** (Choi, Brunet & How, 2009) — a distributed market-based assignment that is *provably* convergent and conflict-free under asynchronous, lossy comms. Each agent runs two alternating phases.

### 6.1 State each interceptor *i* keeps
- `x_i` — the target *i* currently believes it holds (or none).
- `y_i[j]` — the **highest known bid** for each target *j* (the "market price").
- `z_i[j]` — **who** *i* believes holds each target *j*.
- `s_i[k]` — a **timestamp vector**: the time of the most recent information *i* has about agent *k*'s bids. **This is the backbone of async robustness.**

### 6.2 Phase 1 — Auction (local, greedy)
Each agent computes its score `s_i(j) = 30 − TTI_i(j)` for every target. It bids on the target that maximizes its score **and on which it can currently win** (its score exceeds the standing market price `y_i[j]`). It records its bid in `y_i`, claims it in `z_i`.

### 6.3 Phase 2 — Consensus (talk to neighbors)
Agents exchange `(y, z, s)` with whatever neighbors they can reach. On receiving `(y_k, z_k, s_k)` from *k*, for each target *j*, *i* applies a **deterministic action rule** — *update / reset / leave* — comparing its own belief to *k*'s:

| *k* asserts (about target *j*) | *i*'s action |
|---|---|
| *k* holds *j* and `y_k[j] > y_i[j]` | **UPDATE** → adopt *k* (higher bid wins) |
| *k* holds *j*, bids equal, `k`'s id lower | **UPDATE** (deterministic tie-break) |
| *i* holds *j* (per *k*) | **LEAVE** (trust own) |
| a third party *m* holds *j*, and *k*'s info is **fresher** (`s_k[m] > s_i[m]`) | **UPDATE** to *k*'s view |
| *k* thinks *j* is unheld but *i* thought *k* held it | **RESET** (release, re-auction) |

If *i* discovers it has been **outbid on the target it holds** (someone's recorded bid now exceeds its own), it **releases** that target and returns to Phase 1 to re-bid. Phases alternate until no agent changes — the assignment has converged.

### 6.4 Why this survives being out of sync (the "async" requirement)
- **Higher-bid-wins + timestamp freshness** is a *commutative, monotone* rule: applying messages in **any order** drives every agent toward the same fixed point. Out-of-order or duplicated messages can't break it.
- **Timestamp vectors `s`** let an agent tell *whose third-party information is newer* without a global clock — the essence of asynchronous consensus.
- **Intermittent links / partitions:** as long as the network is *eventually* connected, beliefs reconcile when links return. Temporary disagreement is bounded and self-healing (see 6.6).
- **No global clock, no master, no barrier** — agents act on whatever they've heard so far.

### 6.5 Worked consensus trace — the t1→t2 retarget (the swap, done distributively)

Entering t2, **A holds T2** (bid 21 from t1), **B holds T1** (bid 22 from t1). Then T2 maneuvers; both recompute local TTIs → scores (`30−TTI`):

- **A:** T1 = 23 (was 16), T2 = 15 (was 21). A now values **T1** highest, and its hold on T2 has collapsed.
- **B:** T1 = 19 (was 22), T2 = 20. B now values **T2** highest.

**Round:**
1. **A** sees T2's value gone; its best is T1 (23). Standing price on T1 is B's 22. `23 > 22` ⇒ A bids T1 at **23**, releases T2.
2. **B** receives A's message: A outbids it on T1 (`23 > 22`) ⇒ B is outbid, **releases T1**. B re-bids its best available, **T2 at 20** (T2 now unheld, since A vacated it).
3. **Exchange:** A broadcasts `{T1: A, 23}`; B broadcasts `{T2: B, 20; T1 released}`. Both adopt: `z = {T1→A, T2→B}`. **No conflict. Converged.**

Result: `A → 1`, `B → 2` — the retarget the scenario calls for, reached with **no station in the loop**, purely by bid + consensus.

### 6.6 Failure modes & self-healing
- **Lost message:** the next exchange carries the same monotone state → eventual convergence. No special retransmit needed (though duplicate-send-with-jitter helps latency).
- **Partition (A↔B link drops mid-swap):** both may briefly believe they hold T1 — **transient double-targeting**. This is the *safe* failure direction (you over-cover a threat, you don't drop one). On re-merge, higher-bid-wins resolves it; the loser releases and re-bids. Bounded waste: at most one redundant interceptor per partition event.
- **Interceptor lost:** its held target's price decays / its claim ages out → the target is re-auctioned to the survivors (D, the reserve, is the natural taker).
- **Stale third party:** an agent C with an old "B holds T1" view adopts the fresher "A holds T1, t2" once it receives it (timestamp `s` decides) — consistency restored without central arbitration.

### 6.7 Convergence & complexity (and why it fits a Pi)
- **Guarantee (CBBA):** converges to a conflict-free assignment within `O(N_targets · network-diameter)` rounds for the static case; re-tasking triggers a bounded incremental re-convergence. Within `(1−1/e)` of optimal for the bundle case; **optimal** for our single-assignment (CBAA) case under diminishing-marginal scores.
- **Per-agent work:** `O(N_targets)` to compute bids + `O(N_targets)` per received message for consensus. Messages are two short vectors + a timestamp vector.
- **Pi-class compute:** trivial — kilobytes of state, microseconds of work per round. The expensive geometry (TTI / `P_kill`) is the **TALOS** lookup (precomputed offline, see TALOS spec §3.8); PHALANX on top is near-free. This composes cleanly with the conservative-compute constraint.

## 7. Composition with TALOS (the #2 stack)

Clean separation of concerns for Challenge #2:

- **TALOS** answers, per (interceptor, target): *can I hit it, how good is the shot, and how do I fly the intercept?* → produces the **score** (TTI / `P_kill` / feasibility).
- **PHALANX** answers, across all interceptors: *who takes whom?* → the **distributed assignment** over those scores, async-consensus-robust.

Together they are a complete, resilient answer to #2: distributed sensor-fed scoring (TALOS) + distributed conflict-free coordination (PHALANX), with no central point of failure.

## 8. Demo plan (very demoable, software-only)

A 2-D map: launch station, 3 incoming target tracks, 4 interceptor icons moving. Side panel shows the **live assignment matrix** updating t0→t3 (and beyond). Arrows show **inter-drone messages and bids**.

1. Targets enter on schedule; drones launch; the matrix fills in and the swap at t1 plays out live.
2. **Maneuver injection:** target 2 jinks → the affected drones re-bid → the matrix swaps back at t2, narrated as a peer-to-peer auction (no station involved).
3. **Comms dropout injection:** kill the A↔B link mid-swap → show transient double-targeting (both glow on T1) → restore link → consensus self-heals to a conflict-free matrix. *This is the money shot: coordination that survives jamming.*
4. **Interceptor loss:** kill C → T3 re-auctioned → **D** (reserve) takes it automatically.

Deterministic scripted scenario for safe rehearsal. Everything is simulated software (drones + lossy message bus), so no hardware dependency.

## 9. Build plan (brief)

- **Fri night:** sim of targets + interceptors + a lossy/async message bus; TTI/score model; render map + matrix panel.
- **Sat AM:** CBAA bid + consensus phases; reproduce t0→t3 exactly; matrix updates live.
- **Sat PM:** timestamp-vector async robustness; inject maneuver / dropout / loss; self-healing.
- **Sat night:** integrate, polish viz of messages & bids, freeze.
- **Sun AM:** rehearse ×3; deck (the matrix evolution + the consensus-under-jamming story).

## 10. Challenge mapping (put this in the deck)

**Challenge #2 — Multi-Interceptor Coordination & Threat Assignment:**

| Asks for | PHALANX delivers |
|---|---|
| Real-time threat assignment, optimal targeting | CBAA consensus auction → conflict-free, (near-)optimal assignment |
| **Coordination algorithms: share targeting data across the interceptor network, minimal latency** | Peer-to-peer bid/consensus exchange — exactly this |
| **Dynamic re-tasking** if priorities change | Score change (maneuver) → release + re-bid → re-converge (t1↔t2 swaps) |
| Distributed sensor fusion | Consumes per-agent TTI/`P_kill` (TALOS / fused tracks) as bid scores |
| Methods: graph optimization, **consensus**, game theory | Consensus-based distributed auction (CBBA), market/auction game |
| Tracks interceptor state & ammo | Reserve handling (D), released/aged claims, re-auction on loss |

**Cross-link — Challenge #8 (Self-Healing Comms):** PHALANX is designed for intermittent, partitioned, lossy links and **self-heals** its assignment when comms degrade — the same contested-environment resilience #8 targets, applied to the *coordination* layer rather than the transport layer.
