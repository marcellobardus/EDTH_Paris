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

## 3. Offline planning vs. online execution

- **Offline (the "planning"):** sample the threat-state space (range × bearing × speed × heading); for each cell, solve the engagement optimization; store the result as (a) an optimal **firing policy** (commit decision + aim) lookup/interpolant, and (b) a precomputed **no-escape envelope** — the region from which a launch *guarantees* a kill before the boundary.
- **Online (the "reflex"):** the live track indexes the policy; the station fires the instant the track enters the commit region, with the precomputed aim — no solver latency, no human. Re-solve / replan (MPC-style) only when the track deviates from prediction beyond a threshold.

This separation **is** the differentiator: the hard optimization happens before the threat is in range; the trigger pull is O(microseconds).

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
