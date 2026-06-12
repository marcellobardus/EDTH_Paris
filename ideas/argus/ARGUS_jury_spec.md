# ARGUS Jury — Evidence-Consensus Algorithm Spec

Scope: the algorithm that decides `CONFIRMED / UNCONFIRMED / DISPUTED` per track, plus a latency analysis of the full sensor→decision path.

## 0. Design constraint that shapes everything

**Zero communication rounds.** Pickets never talk to each other, never vote, never ACK. Every reading is one-way, fire-and-forget: sense → sign → transmit → forget. All consensus computation happens centrally at the gateway on whatever evidence has arrived. This is not a simplification of BFT — it is the correct model: the adversary doesn't attack message ordering, he attacks the correspondence between messages and reality. Agreement on *what was said* is free (hash-chain log); agreement on *what is true* is physics, computed in one place.

Consequence: algorithm latency = 0 network round-trips. All real latency is one-way physical/transport delay (analyzed in §5).

## 1. Parameters (frozen for the hackathon)

| Param | Value | Meaning |
|---|---|---|
| `T_window` | 10 s | evidence window per track |
| `k` | 2 | independent witnesses for CONFIRMED (3 in high-threat config) |
| `D_min` | 50 m | min distance between witnesses to count as independent |
| `geo_tol` | 150 m | max distance of a bearing ray from fused position |
| `v_max / a_max` | per class: shahed 60/10, fpv 50/30, vehicle 40/8, unknown 100/50 (m/s, m/s²) | physical bounds |
| `strike_limit` | 3 strikes / 10 min | lone-dissent count before node is muted |
| `N_clear` | 15 s | clean updates needed to clear DISPUTED |
| `decay` | confirm → unconfirmed if witnesses < k for > T_window | hysteresis down |
| `tick` | 2 Hz | Jury evaluation rate (per track, on gateway) |

## 2. Inputs / outputs

Input per tick, per track: all `auth=VERIFIED` readings associated to the track with `ts` in the last `T_window`, plus the node registry (surveyed positions, flag status) and the track's fused state (position, velocity, class, confirm state).

Output per tick: `(confirm_state, witness_count, dissenters[], reason)`. Reason strings are user-facing: `NO_CORROBORATION`, `KINEMATICS_IMPOSSIBLE`, `GEOMETRY_MISMATCH`, `CONTESTED`.

`UNVERIFIED`/`FAILED` readings never reach the Jury (admission control upstream). A perfect signature earns exactly nothing here — that is the point of the system.

## 3. The algorithm (per track, per tick)

```
STEP 1 — WITNESS SET
  group readings by node_id
  drop nodes that are: flagged (step 5), self-inconsistent
    (same node placing the object >v_max·Δt apart), or
    within D_min of an already-counted witness
  → W = independent witnesses, each with its readings

STEP 2 — PHYSICS VETO (runs before any counting)
  speed(track) > v_max(class)  → DISPUTED(KINEMATICS_IMPOSSIBLE)
  accel(track) > a_max(class)  → DISPUTED(KINEMATICS_IMPOSSIBLE)
  frame jump > v_max·Δt + slack → DISPUTED(KINEMATICS_IMPOSSIBLE)
  # physics is a veto, not a vote: 10 signed readings of a
  # 600 km/h Shahed are 10 liars, not a confirmation

STEP 3 — GEOMETRIC CROSS-EXAMINATION
  for each witness with bearing data:
    ray = (node surveyed position, reported bearing)
    if dist(ray, fused position) > geo_tol:
        move witness from W to DISSENT, strike(node)
  if |DISSENT| >= |W| and |DISSENT| > 0 → DISPUTED(GEOMETRY_MISMATCH)

STEP 4 — QUORUM
  |W| >= k                  → candidate CONFIRMED
  |W| == 1                  → UNCONFIRMED   # repetition by one
                                            # witness is never
                                            # corroboration
  W nonempty, DISSENT nonempty, neither dominates
                            → DISPUTED(CONTESTED)

STEP 5 — DISSENT ACCOUNTING (reputation)
  node ends up lone asserter of a track no one corroborates,
  or geometric dissenter against a quorum → strike(node)
  strikes(node) >= strike_limit within 10 min →
      node flagged: still logged, still displayed,
      weight 0 in all quorums until operator clears.
  # this is how a captured picket mutes itself automatically,
  # with no revocation infrastructure

STEP 6 — HYSTERESIS (state machine)
  DISPUTED is sticky: clears only after N_clear seconds of
    k-corroborated, physics-valid updates with all flagged
    nodes excluded
  CONFIRMED decays to UNCONFIRMED after T_window below quorum
  transitions limited to 1 per 2 s per track → no flapping
    on the operator's screen
```

Complexity per tick: O(readings in window) per track — microseconds on a laptop. The Jury is never the bottleneck.

### State machine

```
UNCONFIRMED ──(|W|≥k, physics ok, geometry ok)──► CONFIRMED
UNCONFIRMED/CONFIRMED ──(any veto/contest)──────► DISPUTED (sticky)
CONFIRMED ──(quorum lost > T_window)────────────► UNCONFIRMED
DISPUTED ──(N_clear clean, suspects excluded)───► UNCONFIRMED
```

Firing-quality = `auth VERIFIED` on all contributors AND `confirm CONFIRMED`. Hard rule, enforced in one function, no override path in v0.

## 4. What this defeats / what it doesn't

| Scenario | Outcome |
|---|---|
| 1 captured key, fabricated track | Lone witness → UNCONFIRMED at best; geometry/physics usually → DISPUTED; node accumulates strikes → muted |
| 1 captured key, *shadowing a real track* with subtle offsets | Geometry check catches offsets > geo_tol; below that, the lie is bounded to ≤150 m — state this honestly |
| 2+ captured keys, colluding with consistent geometry (k=2) | **Defeated only by raising k to 3** or operator review — the fundamental limit of k-of-n. Roadmap: per-node secure elements raise the cost of each capture |
| Jamming / lost readings | Tracks decay to UNCONFIRMED. Degradation is always toward *less* trust, never toward false trust |
| Sensor honestly wrong (wind, echo) | Same handling as malice — the Jury doesn't distinguish lying from broken, by design |

## 5. Latency analysis — is back-and-forth a problem?

There is no back-and-forth. The end-to-end path is a one-way pipeline; the budget below is detection → operator screen, worst-case realistic values:

| Stage | Latency | Notes |
|---|---|---|
| Sound propagation to picket | **0.3–9 s** | 343 m/s; 100 m → 0.3 s, 3 km → 8.7 s. **Dominates everything.** Physics, not architecture |
| On-node DSP (FFT window + persistence check) | 0.5–1.5 s | tunable; shorter = more false positives |
| Ed25519 sign on ESP32 (240 MHz, Monocypher) | 20–60 ms | negligible |
| LoRa airtime, 135 B frame | SF7: ~0.23 s · SF9: ~0.8 s | choose SF7/SF8 at picket-to-gateway ranges ≤2–3 km |
| Gateway verify (sig + counter + ts) | <1 ms | libsodium verify ~50 µs |
| Log append + fusion associate | <1 ms | |
| Jury tick | <1 ms, runs at 2 Hz → adds ≤0.5 s scheduling delay | |
| WebSocket → COP render | ~50 ms | |
| **Total, picket at 1 km from threat** | **≈ 4–6 s** | of which ~3 s is the speed of sound |

**Against the threat:** a Shahed flies 50–85 m/s. A 5 s pipeline means the map lags reality by 250–425 m — about one map pixel at operational zoom, and the fusion engine's velocity estimate lets the COP dead-reckon the displayed position forward to compensate (cheap, do it). Acoustic detection radius ~2–5 km per picket gives 30–90 s of warning per picket line; the pipeline consumes <10% of it.

**Time-to-CONFIRMED** adds the wait for a second witness. This is geometry, not protocol: the drone must enter a second picket's detection radius. With the spec'd 1–3 km picket spacing along the approach corridor, a 60 m/s Shahed reaches the second picket 15–50 s after the first — so expect tracks to live as UNCONFIRMED for tens of seconds before flipping CONFIRMED. **This is correct behavior, not lag**: the system is honestly displaying single-source intelligence as single-source. Denser spacing buys faster confirmation linearly; that's a deployment-cost knob, not an algorithm change.

**The one real transport constraint — LoRa duty cycle.** EU 868 MHz sub-bands allow 1% airtime (most) or 10% (869.4–869.65 MHz, 500 mW). At SF7 (~0.23 s/frame): 1% ⇒ one frame per ~23 s per picket — too slow for tracking. **Use the 10% sub-band ⇒ one frame per ~2.3 s per picket**, i.e. ~0.4 Hz update rate. That's sufficient: corroboration needs k witnesses within a 10 s window, not a high frame rate, and fusion tolerates 2–3 s gaps at Shahed speeds. For FPV-speed targets at close range, pickets fall back to WiFi/ESP-NOW where available (transport-agnostic envelope — nothing else changes).

**Retransmission policy:** no ACKs, no ARQ (an ACK channel would create the round-trip dependency we refuse, and doubles each picket's RF signature). Instead: send every detection frame **twice**, 300–700 ms apart with random jitter. Gateway dedups by `(node_id, ctr)` — the counter you already have for replay protection does double duty. Loss of both copies degrades to UNCONFIRMED, which is the safe direction.

**Clock sync:** freshness window is ±30 s, so NTP-at-boot is sufficient; drift of seconds is harmless. The Jury never compares timestamps across nodes at sub-second precision — that's why v0 deliberately uses bearing geometry, not TDoA (TDoA would demand ms-level sync across pickets, a real distributed-systems problem, and is exactly the kind of scope to leave on the roadmap).

### Verdict

Latency is a non-issue *because of* the algorithm's shape: zero rounds, one-way flows, central evaluation. The pipeline is dominated by the speed of sound, which no protocol fixes; the only engineered constraint is LoRa duty cycle, solved by the 10% sub-band + low required update rate. The slowest thing in the system is intentionally the *epistemics* — waiting for a second independent witness — and that wait is the feature being sold.

## 6. Roadmap (one slide, only if asked)

- k as a per-zone policy (2 rear, 3 forward).
- Weighted quorums: witness weight = f(sensor modality diversity) — one mic + one camera > two mics.
- Gateway availability: 3× replicas + Raft (boring, solved). Gateway *trust*: threshold-sign the fused COP 2-of-3 across replicas so a single compromised command post cannot forge the picture downstream — the only place a real consensus protocol earns its complexity in this system: above the Jury, never below it.
