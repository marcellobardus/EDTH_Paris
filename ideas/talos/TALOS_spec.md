# TALOS — Autonomous Interception Planner

**Project spec — European Defense Tech Hackathon, Paris, June 12–14 2026**
**Challenge fit:** primarily **Alta Ares #2 — Real-Time Multi-Interceptor Coordination and Threat Assignment** (its engagement-optimization core); adjacent to **#3** (autonomous engagement / feasibility) and consumes tracks from **#4 / ARGUS**.
**Name:** *Talos* — the bronze automaton that guarded Crete, hurling stones at approaching enemy ships without a human in the loop. The original autonomous interception station.

**Concept:** Given a tracked incoming threat (a Shahed-class drone) and an interceptor station with a known weapon envelope, TALOS solves the *engagement optimization* — **when to fire, where to aim, and whether to commit now or wait** — to maximize kill probability subject to killing the threat before it crosses a keep-out boundary, and pulls the trigger **autonomously, with zero human latency.** Offline it precomputes the optimal firing policy over the threat corridor; online it looks the policy up and refines it, committing the optimal shot the instant the math says so.

---

## 1. Problem framing — this is an optimization problem, not a detection one

The scenario, concretely: a Shahed-class one-way attack drone is inbound on a defended asset at ~50–85 m/s. Between it and the asset sits **one interceptor station** (gun, micro-missile, or interceptor drone). The decision *fire-now / wait / where-to-aim* must be made faster than a human can, repeatedly, as the track updates — and it must **guarantee** the kill happens before the threat reaches a minimum-safe boundary around the asset.

What this idea is **not**:
- Not detection/tracking — assume the track is given (from ARGUS / Challenge #4).
- Not friend-foe/ROE — assume the target is validated hostile (that's Challenge #3, an upstream dependency).

What it **is**: an **optimal-control / optimization problem** — choose the engagement decision variables to maximize expected kill probability subject to hard kinematic and safety constraints.

**Why "offline planning":** the engagement-time budget is seconds and a heavy solver-in-the-loop adds latency and failure modes. TALOS does the hard optimization *offline* — precomputing a firing policy over the space of threat states — so the station executes *online* as a reflex, in microseconds, with no human. **Plan offline, execute reflexively online** — the same split real fire-control and robotics motion-planning use to keep heavy compute out of the real-time loop.

**Pitch line:** *"By the time a Shahed is in range, the decision is already made. TALOS solves the kill geometry before the threat arrives, so the trigger pull is a reflex — optimal, autonomous, and provably in time."*

## 2. The optimization (the heart of it)

**Decision variables**
- `t_fire` — the commit time (when to launch)
- aim point / lead — the predicted intercept point (hence launch direction)
- salvo size & spacing (multi-shot)
- *(multi-threat → #2)* which interceptor engages which threat

**Objective:** maximize expected kill probability `E[P_kill]`, where `P_kill` is a function of the engagement geometry at intercept (closing speed, aspect angle, miss-distance distribution vs. warhead lethal radius).

**Hard constraints**
- **Kill-before-boundary:** intercept must occur before the threat crosses keep-out radius `R_min` around the asset — chance-constrained: `P(kill before boundary) ≥ α` (e.g. 0.95).
- **Reachability:** intercept point inside the interceptor's launch envelope (max/min range, flyout time vs. interceptor speed & turn rate).
- **Ammunition** limit.
- *(optional)* collateral / debris footprint.

**Sub-problems**
1. **Threat prediction** — propagate the track forward with a motion model (constant-velocity baseline; constant-turn / process-noise for a maneuvering Shahed) → predicted position + a growing uncertainty ellipse over time.
2. **Intercept-point solution** — for a given launch time, solve the collision triangle for where interceptor and threat meet (closed-form for constant velocities; iterative / proportional-navigation for an accelerating intercept). Yields flyout time and launch geometry.
3. **Commit-time optimization — the crux.** Fire *early* → more reaction margin but larger threat-position uncertainty (it can still maneuver) ⇒ lower `P_kill`. Fire *late* → tighter prediction (higher `P_kill`) but risks missing the keep-out deadline. The optimal `t_fire` maximizes `E[P_kill]` pushed right up against the latest-feasible-launch constraint. A 1-D optimization (golden-section / line search) per track update — or precomputed as a policy.
4. **Uncertainty** — Monte-Carlo the threat motion model (or propagate the covariance, unscented) to compute `E[P_kill]` and chance-constraint satisfaction, instead of optimizing a single deterministic trajectory. This is what makes it robust to a jinking drone.

**Methods (name these in the deck):** proportional-navigation guidance for flyout, collision-triangle / lead-angle intercept solution, golden-section / MPC for commit timing, Monte-Carlo or unscented propagation for uncertainty, chance-constrained optimization for the safety guarantee.

**Multi-threat extension → Challenge #2:** wrap each threat's optimal-engagement *value* into an assignment problem across interceptors — **Hungarian / max-flow** with engagement probability, range, and reload/ammo as costs; re-solve every tick (dynamic re-tasking). This is exactly #2's stated toolbox.

## 3. Offline planning vs. online execution (the core architecture)

### 3.1 Why split at all

The engagement loop must close inside the sensor-update budget — single-digit milliseconds — and it must **never stall**. But the decision it makes is the output of a heavy stochastic optimization: predict a maneuvering threat, Monte-Carlo its future, solve the intercept geometry, line-search the commit time against a chance constraint. You cannot run that every tick, and you must not let an autonomous weapon depend on a solver with variable runtime that can fail to converge mid-engagement.

So TALOS splits the problem across **three timescales**:

| Timescale | When it runs | What runs | Budget |
|---|---|---|---|
| **Offline (slow)** | at setup, and on slow-variable change | full global optimization → policy + envelopes + tables | seconds–minutes |
| **Online (fast)** | every track update | lookup + commit check | < 1 ms |
| **Online (exception)** | only on track deviation | warm-started *local* replan | tens of ms |

The expensive thinking happens before the threat is in range; the trigger pull is a reflex; a cheap local re-solve covers the surprises. This is the **explicit/implicit MPC** pattern — a precomputed control law for the common case, a bounded online solve for the exceptions.

### 3.2 What "offline" produces

*Offline* means "computed ahead of the time-critical loop," not "disconnected." For the station's weapon + the defended asset's geometry, TALOS precomputes:

1. **No-Escape Envelope (NEZ)** — the set of threat states (relative position, velocity, heading) from which a launch *guarantees* a kill before the keep-out boundary, with margin. A static volume for a fixed geometry — the headline visual *and* the graceful-degradation fallback.
2. **Firing policy** `π(threat_state) → {commit?, aim, salvo}` — the optimal action for every discretized threat state. An *explicit* control law: the optimization solved once and tabulated.
3. **Flyout / time-of-flight & `P_kill` tables**, keyed on launch geometry — so online you never integrate interceptor dynamics or recompute lethality; you read them off.
4. **Launch-Acceptability Region (LAR)** — the set of feasible intercept points; bounds the commit decision.

Because this stage is offline it can afford what the online loop cannot: dense Monte-Carlo over maneuver models, robust / chance-constrained optimization, full lethality modelling. **The quality of the decision is set here.**

### 3.3 What "online" does

Per track update (10–50 Hz):
1. Estimate the current threat state + uncertainty from the new track point.
2. **Index** the policy / NEZ — O(1) lookup + interpolation, *no optimization*.
3. **Commit logic:** if the threat is inside the commit region **and** the latest-feasible-launch deadline has arrived → fire with the precomputed aim. Otherwise keep tracking.
4. **Deviation gate:** compare the actual track to the trajectory the policy assumed. Within tolerance → trust the lookup. Beyond tolerance (unexpected maneuver, off-grid state) → trigger the exception path.

The loop cost is dominated by the track filter, not the decision — the decision is a table read.

### 3.4 The exception path (online replan)

When reality leaves the precomputed manifold, TALOS does **not** rerun the full global sweep. It runs a **local, warm-started** re-solve: seed the optimizer with the nearest offline solution and take 1–2 MPC iterations to refine. Bounded time, fast convergence because it starts near-optimal. This is what stops a jinking Shahed from invalidating the plan while preserving the real-time guarantee.

### 3.5 Staleness — when offline gets recomputed

The policy is valid only under its assumptions (station position, ammo count, weapon health, wind/met, threat-class library). When a **slow** variable changes, a background process recomputes the policy and hot-swaps it — and because it's offline, a multi-second recompute is free. Triggers: emplacement, asset move, ammo depletion, met update, new threat type. **Fast** variables (the live track) are handled entirely by the online lookup. The design art is *which variables to bake into the offline policy vs. handle online*: TALOS bakes geometry & weapon characteristics, leaves kinematics to the lookup.

### 3.6 Why this makes multi-threat (#2) tractable

The payoff for Challenge #2: each threat's engagement *value* (max `E[P_kill]`, feasibility, time-window) is a **precomputed lookup**, not a nested optimization. So the online multi-interceptor assignment (Hungarian / max-flow) runs over N×M *cheap* values every tick instead of solving N×M intercept problems live. **Offline precompute is exactly what keeps the real-time coordination real-time.**

### 3.7 The pitch, distilled

- **Latency:** *"the trigger is microseconds because the thinking happened beforehand."*
- **Certifiability:** *"no unbounded solver in the kill chain — the policy is verified offline, bounded-time online."*
- **Robustness:** *"expensive uncertainty quantification we could never afford live is baked into the policy."*
- **Graceful degradation:** *"compute-starved or sensors flaky? fall back to the no-escape envelope — in the zone, fire."*

This separation **is** the differentiator: the hard optimization happens before the threat is in range; the trigger pull is O(microseconds).

### 3.8 Mapping to Raspberry-Pi-class compute (the constraint this architecture is built for)

Deployment target is a Raspberry Pi (Pi 5: 4× Cortex-A76 @ 2.4 GHz, 4–8 GB). This is exactly the hardware story the offline/online split exists for — and it forces a few concrete decisions:

- **Offline runs *off* the Pi.** The expensive global optimization (Monte-Carlo, chance-constrained search, SciPy) runs on a laptop at setup — or is precomputed before the demo. The Pi only ever *loads the resulting tables*; it never runs the heavy solver.
- **The online loop is trivial for a Pi.** Per tick: one track-filter update (Kalman, microseconds), one table lookup + interpolation (nanoseconds–microseconds), the commit check (a few comparisons). At 50 Hz that's well under 1 ms on a fraction of one core — leaving the other three cores free for multiple simultaneous threats.
- **Keep the table small via symmetry.** A naive grid over full 3D position + velocity blows up memory. Exploit the station's rotational/translational symmetry: work in *relative* coordinates — range, aspect angle, closing speed (+ altitude band). The state space collapses from ~6D to ~3–4D: a 50×36×30 grid is ~54k cells × ~16 B ≈ **under 1 MB**, resident in RAM and cache-friendly. (Alternative: fit a few-KB polynomial / tiny MLP to the offline solutions — a forward pass is microseconds on a Pi.)
- **Bound or drop the online replan.** The exception path (§3.4) is the only piece that would run optimization *on the Pi*. **Conservative choice: omit it** and instead pad the No-Escape Envelope with a margin sized for the worst-case maneuver — then even "surprises" are handled by pure lookup against a slightly conservative envelope, with **no solver in the loop at all**. If you keep the replan, cap it at a 1-D line search with a hard iteration/time budget. Either way the Pi never runs an unbounded solve.
- **No heavy linear algebra, no per-tick allocation.** The hot path is table reads + a fixed-size filter. Preallocate; avoid Python object churn (NumPy arrays, or a small Cython/C core for the filter if needed). The decision is memory-bound, not compute-bound.

**Net:** the Pi runs a *reflex* — read sensor, read table, maybe fire. Every genuine computation lives offline on a real machine. This is the strongest argument *for* the architecture: on weak hardware a solver-in-the-loop design **cannot** meet the real-time guarantee, while the precomputed-policy design runs comfortably with cores to spare.

## 4. Architecture / components

| Component | Stack | Does |
|---|---|---|
| `trackin/` | Python | consumes a threat track (ARGUS/#4 or sim): position, velocity, class, uncertainty |
| `predict/` | Python + NumPy | motion-model propagation + uncertainty (CV/CT + Monte-Carlo) |
| `optimize/` | Python + SciPy | intercept-point solve + commit-time optimization + chance constraint |
| `policy/` | Python | offline sweep of threat-state space → firing policy + no-escape envelope |
| `firecontrol/` | Python | autonomous commit logic: monitor track, fire at optimal `t_fire`, enforce ammo / keep-out / ROE gate |
| `assign/` (→#2) | Python | multi-threat × multi-interceptor Hungarian/max-flow over engagement values |
| `sim+viz/` | Python + web (React/Canvas or deck.gl) | 2D/3D engagement: threat track, predicted path + uncertainty cone, intercept point, interceptor flyout, **kill-prob-vs-commit-time curve**, commit indicator, no-escape envelope |

Single process for predict+optimize+firecontrol; web frontend separate. Resist over-engineering.

> **On-Pi vs off-Pi (see §3.8):** the offline stages — `optimize/`, `policy/`, and the Monte-Carlo in `predict/` — run on a laptop at setup and ship tables to the target. Only `trackin/`, the online `predict/` lookup, `firecontrol/`, `assign/`, and the viz run on the Raspberry Pi during the engagement.

## 5. Demo (4 min)

1. **(30 s)** Problem: a Shahed is 8 km out and closing; one station, seconds to decide, no time for a human — and under saturation no human can keep up.
2. **(75 s)** Single engagement: the threat track comes in; TALOS draws the predicted path + uncertainty cone, the computed intercept point, and a live **kill-probability-vs-commit-time curve** with the optimal commit marked. At the optimal instant it **autonomously fires**; the interceptor flies out (PN) and kills the threat *before* the keep-out ring. Nobody touched it.
3. **(45 s)** Maneuvering threat: the Shahed jinks → prediction & uncertainty update → optimizer re-commits → still intercepts. Robustness shown, not claimed.
4. **(30 s)** **No-escape envelope:** overlay the precomputed region — *"anything that enters this is already dead; that decision was made offline, before it arrived."*
5. **(45 s)** Multi-threat (→#2): 3 Shaheds, 2 interceptors; the assignment layer allocates shots to maximize total kills under ammo limits and re-tasks as priorities shift.
6. **(15 s)** Pathway: real interceptor dynamics, hardware-in-the-loop, sensor/track integration, ROE gating (ties to #3).

## 6. Build plan (Fri 18:00 → Sun 12:00, team of 3–4)

- **Fri night:** sim of threat + asset + keep-out ring; track schema; constant-velocity predictor; 2D viz renders one engagement.
- **Sat AM:** intercept-point solver + PN flyout; `P_kill` model; single-threat commit-time optimization; render the `P_kill` curve.
- **Sat PM:** uncertainty (Monte-Carlo) + chance constraint; offline policy precompute + no-escape envelope; autonomous fire-control loop.
- **Sat night:** multi-threat assignment (#2 extension); integration; freeze at midnight.
- **Sun AM:** rehearse ×3; deck (the optimization, the offline/online split, requirement map).

**Hard rule:** anything not demoable by Saturday midnight becomes a slide.

## 7. Risks & fallbacks

| Risk | Fallback |
|---|---|
| Optimizer too slow to run live | That's the entire point of offline precompute — online is a lookup, not a solve |
| Looks like a toy animation | Ground every number in real Shahed speeds & plausible interceptor envelopes; show the math (P_kill curve, envelope), not just motion |
| "Where's the autonomy ethics story?" | Autonomy here is the *firing solution*; target validation & ROE are #3 and an explicit upstream dependency — humans authorize the engagement zone, TALOS optimizes the shot within it |
| Multi-threat too ambitious | Keep single-threat rock-solid as the core; multi-threat is the #2 bonus, cut to a slide if needed |

## 8. What makes this win

Most #2 teams will hand-wave the engagement physics and build a dashboard with an assignment heuristic. TALOS shows the part of air defense that is *genuinely* an optimization problem — a live kill-probability curve, a provable no-escape envelope, an autonomous commit that beats human reaction time — and the offline/online split that makes it real-time deployable. It's the hard core, solved and demonstrated, not described.

## 9. Ecosystem fit (ARGUS / LEUKO / TALOS)

Clean division of labour across the idea family — *the network cross-examines itself, then acts*:
- **ARGUS (#3/#4):** is the track real, and should we shoot it? (validation + intent)
- **TALOS (#2):** given we should — *how* do we shoot it optimally and autonomously? (engagement optimization)
- **LEUKO (#7/#8):** is our own sensor/comms fabric healthy while we do it? (immune layer)

TALOS consumes ARGUS's firing-quality tracks directly — pick any two and they compose into a coherent demo story.

## 10. Requirement mapping (put this in the deck)

**Challenge #2 — Multi-Interceptor Coordination & Threat Assignment:**

| Asks for | TALOS delivers |
|---|---|
| Distributed sensor fusion | Consumes fused tracks (ARGUS/#4) as input |
| Real-time threat assignment, optimal targeting | Hungarian/max-flow over per-threat engagement values |
| Coordination across interceptors | Multi-interceptor assignment layer |
| Dynamic re-tasking mid-engagement | MPC-style replan each tick as priorities/tracks change |
| Tracks ammo & interceptor state | Ammo/reload as constraints in the optimization |
| Firing recommendations with confidence | `P_kill` per engagement = the confidence score |
| Methods: Hungarian, max-flow, game theory | Used directly for the assignment + commit optimization |

**Adjacent — Challenge #3:** *"Evaluate feasibility: intercept geometry, collateral risk, ammunition"* — this is exactly TALOS's constraint set; TALOS is the feasibility-and-execution layer below #3's validate/intent layer.
